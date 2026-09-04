"""Conservative parser for user-authored direct #include directives."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class IncludeError:
    line: int
    code: str
    value: str


def _strip_comments(source: str) -> str:
    out: list[str] = []
    index = 0
    in_block = False
    while index < len(source):
        if in_block:
            if source.startswith("*/", index):
                out.extend("  ")
                index += 2
                in_block = False
            else:
                out.append("\n" if source[index] == "\n" else " ")
                index += 1
        elif source.startswith("/*", index):
            out.extend("  ")
            index += 2
            in_block = True
        elif source.startswith("//", index):
            while index < len(source) and source[index] != "\n":
                out.append(" ")
                index += 1
        else:
            out.append(source[index])
            index += 1
    return "".join(out)


def validate_direct_includes(source: str, allowed: set[str]) -> list[IncludeError]:
    cleaned = _strip_comments(source).replace("\\\n", " ")
    errors: list[IncludeError] = []
    directive = re.compile(r"^[ \t]*#[ \t]*include[ \t]+(.+?)[ \t]*$", re.MULTILINE)
    for match in directive.finditer(cleaned):
        value = match.group(1).strip()
        line = cleaned.count("\n", 0, match.start()) + 1
        if value.startswith('"') and value.endswith('"'):
            errors.append(IncludeError(line, "local_header_forbidden", value[1:-1]))
            continue
        if value.startswith("<") and value.endswith(">"):
            name = value[1:-1].strip()
            if name.startswith("/") or ".." in name.split("/"):
                errors.append(IncludeError(line, "absolute_header_forbidden", name))
            elif name not in allowed:
                errors.append(IncludeError(line, "header_not_allowed", name))
            continue
        errors.append(IncludeError(line, "dynamic_header_forbidden", value))
    return errors
