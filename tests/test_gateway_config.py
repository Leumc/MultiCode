import pytest

from remote_dev.gateway_config import GatewayUser, render_caddyfile


ALICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BOB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_gateway_routes_are_loopback_uuid_scoped_and_forward_authenticated():
    text = render_caddyfile([
        GatewayUser(ALICE, 9101),
        GatewayUser(BOB, 9102),
    ])
    assert "127.0.0.1:9080" in text
    assert "forward_auth 127.0.0.1:9000" in text
    assert f"handle /u/{ALICE}/*" in text
    assert "reverse_proxy 127.0.0.1:9101" in text
    assert f"handle /u/{BOB}/*" in text
    assert "reverse_proxy 127.0.0.1:9102" in text
    assert "0.0.0.0" not in text


@pytest.mark.parametrize("public_id", ["../root", "Alice", "alice", "bad/name", "A" * 36])
def test_gateway_rejects_non_uuid_instance_identifiers(public_id):
    with pytest.raises(ValueError):
        GatewayUser(public_id, 9101)


@pytest.mark.parametrize("port", [80, 8765, 9000, 9080, 9100, 9104, 70000])
def test_gateway_rejects_reserved_or_invalid_ports(port):
    with pytest.raises(ValueError):
        GatewayUser(ALICE, port)
