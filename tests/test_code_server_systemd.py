from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UNIT_PATH = PROJECT_ROOT / "deploy/systemd/code-server@.service"


def unit_lines() -> list[str]:
    return [
        line.strip()
        for line in UNIT_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", ";"))
    ]


def values(name: str) -> list[str]:
    prefix = f"{name}="
    return [line.removeprefix(prefix) for line in unit_lines() if line.startswith(prefix)]


def exec_start() -> str:
    lines = UNIT_PATH.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("ExecStart="))
    command: list[str] = []
    for line in lines[start:]:
        command.append(line.strip().removesuffix("\\").strip())
        if not line.rstrip().endswith("\\"):
            break
    return " ".join(command).removeprefix("ExecStart=")


def test_instances_use_dynamic_users_without_permanent_rdp_accounts():
    text = UNIT_PATH.read_text(encoding="utf-8")

    assert values("DynamicUser") == ["yes"]
    assert not values("User")
    assert not values("Group")
    assert "rdp-" not in text


def test_dynamic_user_storage_is_namespaced_by_immutable_instance_uuid():
    state_directories = " ".join(values("StateDirectory"))

    assert "remote-dev/workspaces/%i" not in state_directories.split()
    assert "remote-dev/code-server/%i" in state_directories.split()
    assert "remote-dev/code-server/%i/User" in state_directories.split()
    assert values("RuntimeDirectory") == ["remote-dev/code-server/%i"]
    assert values("CacheDirectory") == ["remote-dev/code-server/%i"]
    assert values("StateDirectoryMode") == ["0700"]
    assert values("RuntimeDirectoryMode") == ["0700"]
    assert values("CacheDirectoryMode") == ["0700"]
    assert "REMOTE_DEV_INSTANCE_UUID=%i" in values("Environment")
    assert values("WorkingDirectory") == ["/workspace"]
    assert values("MountImages") == [
        "/var/lib/remote-dev/workspaces/%i.img:/workspace:nosuid,nodev,noexec"
    ]


def test_locked_ui_configuration_hides_restricted_core_views_and_gallery():
    import json

    settings = json.loads(
        (PROJECT_ROOT / "deploy/code-server/locked-settings.json").read_text()
    )
    assert settings["extensions.gallery.enabled"] is False
    assert "EXTENSIONS_GALLERY={}" in values("Environment")
    assert settings["workbench.activityBar.location"] == "hidden"
    assert settings["workbench.secondarySideBar.defaultVisibility"] == "hidden"
    assert settings["chat.disableAIFeatures"] is True

    keybindings = json.loads(
        (PROJECT_ROOT / "deploy/code-server/locked-keybindings.json").read_text()
    )
    restricted_keys = {
        "ctrl+`", "ctrl+shift+`", "f5", "ctrl+f5", "ctrl+shift+b",
        "ctrl+shift+d", "ctrl+shift+x", "ctrl+shift+g",
    }
    assert {item["key"] for item in keybindings} == restricted_keys
    assert {item["command"] for item in keybindings} == {"remoteDev.blocked"}

    manifest = json.loads((PROJECT_ROOT / "extension/package.json").read_text())
    assert manifest["capabilities"]["untrustedWorkspaces"]["supported"] is True
    command_ids = {item["command"] for item in manifest["contributes"]["commands"]}
    assert {"remoteDev.submit", "remoteDev.blocked"} <= command_ids


def test_lockdown_hook_is_loaded_from_atomic_current_release():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert (
        "NODE_OPTIONS=--require=/opt/remote-dev/current/code-server/lockdown-spawn.cjs"
        in text
    )
    assert "NODE_OPTIONS=--require=/opt/remote-dev/lockdown-spawn.cjs" not in text


def test_shared_code_server_bypasses_shell_wrapper_and_uses_bundled_node():
    command = exec_start()

    assert command.startswith("/opt/code-server/lib/node /opt/code-server ")
    assert "/opt/code-server/bin/code-server" not in command
    assert "--user-data-dir /var/lib/remote-dev/code-server/%i" in command
    assert "--extensions-dir /opt/remote-dev/extensions" in command
    assert command.endswith("/workspace")


def test_root_managed_instance_config_only_supplies_loopback_port():
    command = exec_start()

    assert values("EnvironmentFile") == ["/etc/remote-dev/code-server/%i.env"]
    assert "--bind-addr 127.0.0.1:${CODE_SERVER_PORT}" in command
    assert "0.0.0.0" not in command


def test_submit_token_uses_systemd_credential_not_environment_or_argv():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert values("LoadCredential") == [
        "remote-dev-api-token:/etc/remote-dev/credentials/%i.token"
    ]
    assert "REMOTE_DEV_API_TOKEN=" not in text
    assert "remote-dev-api-token" not in exec_start()


def test_browser_upload_route_is_disabled_without_disabling_workspace_trust():
    command = exec_start()

    assert "--disable-file-uploads" in command
    assert "--disable-workspace-trust" not in command


def test_extensions_and_locked_editor_configuration_are_read_only():
    read_only = " ".join(values("ReadOnlyPaths"))
    bind_read_only = values("BindReadOnlyPaths")
    inaccessible = " ".join(values("InaccessiblePaths"))

    assert "/opt/code-server" in read_only.split()
    assert "/opt/remote-dev/extensions" in read_only.split()
    assert (
        "/etc/remote-dev/code-server/locked-settings.json:"
        "/var/lib/remote-dev/code-server/%i/User/settings.json"
    ) in bind_read_only
    assert (
        "/etc/remote-dev/code-server/locked-keybindings.json:"
        "/var/lib/remote-dev/code-server/%i/User/keybindings.json"
    ) in bind_read_only
    assert "-/var/lib/remote-dev/code-server/%i/extensions" in inaccessible.split()
    assert "-/workspace/.vscode/extensions" in inaccessible.split()
    assert (
        "/opt/code-server/lib/vscode/node_modules/node-pty/build/Release/pty.node"
        in inaccessible.split()
    )


def test_node_tcp_clients_can_only_reach_the_submit_api():
    assert "REMOTE_DEV_ALLOWED_CONNECTS=127.0.0.1:9000,[::1]:9000" in values("Environment")


def test_sensitive_builtin_extensions_are_disabled_before_extension_host_start():
    environment = values("Environment")
    skipped = next(
        value.removeprefix("VSCODE_SKIP_BUILTIN_EXTENSIONS=").split(",")
        for value in environment
        if value.startswith("VSCODE_SKIP_BUILTIN_EXTENSIONS=")
    )
    required = {
        "vscode.debug-auto-launch",
        "vscode.debug-server-ready",
        "vscode.git",
        "vscode.git-base",
        "vscode.github",
        "vscode.github-authentication",
        "vscode.ipynb",
        "vscode.builtin-notebook-renderers",
        "ms-vscode.js-debug",
        "ms-vscode.js-debug-companion",
        "ms-vscode.vscode-js-profile-table",
        "vscode.simple-browser",
        "vscode.terminal-suggest",
        "vscode.tunnel-forwarding",
    }

    assert required <= set(skipped)
    command = exec_start()
    for extension_id in required:
        assert f"--vscode-option disable-extension={extension_id}" in command


def test_service_network_policy_denies_everything_except_localhost():
    assert values("IPAddressDeny") == ["any"]
    assert values("IPAddressAllow") == ["localhost"]
    assert set(values("RestrictAddressFamilies")[0].split()) == {
        "AF_UNIX",
        "AF_INET",
        "AF_INET6",
    }
