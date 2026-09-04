from remote_dev.headers import validate_direct_includes


def test_allows_enabled_standard_headers_and_bits():
    source = """#include <iostream>\n#include <bits/stdc++.h>\nint main(){}\n"""
    result = validate_direct_includes(source, {"iostream", "bits/stdc++.h"})
    assert result == []


def test_rejects_local_absolute_and_disabled_headers():
    source = """#include \"local.h\"\n#include </tmp/escape.h>\n#include <vector>\n"""
    errors = validate_direct_includes(source, {"iostream"})
    assert {error.code for error in errors} == {
        "local_header_forbidden", "absolute_header_forbidden", "header_not_allowed"
    }


def test_handles_whitespace_and_line_continuation_without_matching_comments():
    source = """// #include <evil.h>\n/* #include <evil2.h> */\n# include \\\n <iostream>\n"""
    assert validate_direct_includes(source, {"iostream"}) == []


def test_rejects_macro_include_expression():
    source = "#define H <iostream>\n#include H\n"
    errors = validate_direct_includes(source, {"iostream"})
    assert len(errors) == 1
    assert errors[0].code == "dynamic_header_forbidden"
