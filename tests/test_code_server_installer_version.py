from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "deploy" / "scripts" / "install-code-server.sh"
PREPARE = Path(__file__).parents[1] / "deploy" / "scripts" / "prepare-host.sh"
RELEASE_INSTALL = Path(__file__).parents[1] / "deploy" / "scripts" / "install.sh"


def test_installer_parses_current_single_line_version_output():
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'EXPECTED_VERSION_LINE="4.135.0 de89acbcdce9d9b870008a270c9f6466993d91f4 with Code 1.135.0"' in body
    assert 'grep -Fxc -- "$EXPECTED_VERSION_LINE"' in body
    assert "require_commands sha256sum tar install mv rm mktemp grep find chmod" in body
    assert "version_lines[0]" not in body
    assert "version_lines[2]" not in body


def test_installer_publishes_root_owned_tree_readable_by_dynamic_users():
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'find "$STAGE" -type d -exec chmod 0755 {} +' in body
    assert 'find "$STAGE" -type f -perm /111 -exec chmod 0755 {} +' in body
    assert 'find "$STAGE" -type f ! -perm /111 -exec chmod 0644 {} +' in body


def test_prepare_host_parses_current_single_line_version_output():
    body = PREPARE.read_text(encoding="utf-8")
    assert 'EXPECTED_VERSION_LINE="4.135.0 de89acbcdce9d9b870008a270c9f6466993d91f4 with Code 1.135.0"' in body
    assert 'grep -Fxc -- "$EXPECTED_VERSION_LINE"' in body
    assert "version_lines[0]" not in body
    assert "version_lines[2]" not in body


def test_prepare_host_publishes_extension_tree_readable_by_dynamic_users():
    body = PREPARE.read_text(encoding="utf-8")
    assert 'chown -R root:root /opt/remote-dev/extensions' in body
    assert 'find /opt/remote-dev/extensions -type d -exec chmod 0755 {} +' in body
    assert 'find /opt/remote-dev/extensions -type f -perm /111 -exec chmod 0755 {} +' in body
    assert 'find /opt/remote-dev/extensions -type f ! -perm /111 -exec chmod 0644 {} +' in body


def test_release_install_uses_noneditable_application_install():
    body = RELEASE_INSTALL.read_text(encoding="utf-8")
    assert "uv sync" in body
    assert "--no-editable" in body


def test_production_launchers_do_not_use_relocated_console_script_shebang():
    root = Path(__file__).parents[1]
    production_files = [
        root / "deploy/scripts/prepare-host.sh",
        root / "deploy/scripts/bootstrap-instance.sh",
        root / "deploy/systemd/remote-dev-control.service",
        root / "deploy/systemd/remote-dev-worker.service",
        root / "docs/deployment.md",
    ]
    for path in production_files:
        body = path.read_text(encoding="utf-8")
        assert ".venv/bin/remote-dev" not in body
        assert ".venv/bin/python" in body


def test_release_installer_publishes_tree_readable_by_service_users():
    body = RELEASE_INSTALL.read_text(encoding="utf-8")
    assert 'find "$STAGE" -type d -exec chmod 0755 {} +' in body
    assert 'find "$STAGE" -type f -perm /111 -exec chmod 0755 {} +' in body
    assert 'find "$STAGE" -type f ! -perm /111 -exec chmod 0644 {} +' in body
