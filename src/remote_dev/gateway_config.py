"""Generate a deterministic loopback-only Caddy gateway configuration."""

from dataclasses import dataclass
import uuid

_INSTANCE_PORTS = {9101, 9102, 9103}


@dataclass(frozen=True)
class GatewayUser:
    public_id: str
    port: int

    def __post_init__(self) -> None:
        try:
            valid = str(uuid.UUID(self.public_id)) == self.public_id
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("instance identifier must be a canonical UUID")
        if self.port not in _INSTANCE_PORTS:
            raise ValueError("code-server port must be one of 9101, 9102, or 9103")


def render_caddyfile(users: list[GatewayUser]) -> str:
    identities = [user.public_id for user in users]
    ports = [user.port for user in users]
    if len(identities) != len(set(identities)) or len(ports) != len(set(ports)):
        raise ValueError("duplicate instance UUID or port")
    lines = [
        "{", "    admin off", "    auto_https off", "}", "",
        "127.0.0.1:9080 {",
        "    bind 127.0.0.1",
    ]
    for user in sorted(users, key=lambda item: item.public_id):
        prefix = f"/u/{user.public_id}"
        lines.extend([
            f"    redir {prefix} {prefix}/ 308",
            f"    handle {prefix}/* {{",
            "        forward_auth 127.0.0.1:9000 {",
            "            uri /api/auth/forward",
            "            copy_headers X-Remote-User",
            "        }",
            f"        uri strip_prefix {prefix}",
            f"        reverse_proxy 127.0.0.1:{user.port}",
            "    }",
            "",
        ])
    lines.extend([
        "    handle {",
        "        reverse_proxy 127.0.0.1:9000",
        "    }",
        "}",
        "",
    ])
    return "\n".join(lines)
