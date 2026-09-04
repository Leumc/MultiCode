import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "deploy/scripts"
SCRIPT_NAMES = ("install.sh", "install-code-server.sh", "prepare-host.sh", "bootstrap-instance.sh", "verify.sh", "backup.sh", "restore.sh", "rollback.sh")


def script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_all_deployment_scripts_are_strict_root_only_shell_scripts():
    for name in SCRIPT_NAMES:
        body = script(name)
        assert body.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
        assert "require_root" in body
        assert "EUID" in body


def test_shared_validation_rejects_wrong_domain_and_non_uuid():
    common = script("common.sh")
    assert "DOMAIN_RE=" in common
    assert "EXPECTED_DOMAIN" not in common
    assert "validate_domain" in common
    assert "validate_uuid" in common
    assert "[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}" in common
    for name in ("install.sh", "verify.sh"):
        body = script(name)
        assert "validate_domain" in body
        assert "validate_uuid" in body


def test_shared_domain_validation_accepts_fqdn_and_rejects_unsafe_values():
    valid = subprocess.run(
        ["bash", "-c", 'source "$1"; validate_domain code.example.com', "bash", str(SCRIPTS / "common.sh")],
        capture_output=True,
        text=True,
    )
    assert valid.returncode == 0, valid.stderr

    for value in ("localhost", "bad/domain.example", "bad..example", f"a.{'b' * 252}"):
        invalid = subprocess.run(
            ["bash", "-c", 'source "$1"; validate_domain "$2"', "bash", str(SCRIPTS / "common.sh"), value],
            capture_output=True,
            text=True,
        )
        assert invalid.returncode != 0, value


def test_install_uses_immutable_versioned_release_and_atomic_current_symlink():
    body = script("install.sh")
    assert "/opt/remote-dev/releases" in body
    assert "ln -s" in body
    assert "mv -T" in body
    assert "current" in body
    assert "systemctl start" not in body
    assert "systemctl restart" not in body
    assert "systemctl enable" not in body


def test_install_accepts_only_one_to_three_unique_canonical_lowercase_uuids():
    body = script("install.sh")
    common = script("common.sh")
    assert "${#UUIDS[@]} <= 3" in body
    assert "duplicate instance UUID" in body
    assert "declare -A seen_uuids" in body
    assert "[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}" in common
    assert "[0-9a-fA-F]" not in common


def test_install_builds_complete_immutable_runtime():
    body = script("install.sh")
    assert "uv sync" in body and "--frozen" in body and "--no-dev" in body
    assert "sandbox/sandbox_exec.c" in body
    assert "-lseccomp" in body
    assert "remote-dev-submit-0.1.0.vsix" in body
    worker = (ROOT / "deploy/systemd/remote-dev-worker.service").read_text(encoding="utf-8")
    assert "--sandbox-exec /opt/remote-dev/current/libexec/sandbox-exec" in worker


def test_code_server_installer_is_pinned_and_non_overwriting():
    body = script("install-code-server.sh")
    assert "4.135.0" in body
    assert "300ef4e37e469e6368a4673c6a623e1c9ba8a34f42b394fb49c431a8900bc7d1" in body
    assert "sha256sum" in body and "tar" in body
    assert "[[ ! -e /opt/code-server" in body
    assert "curl" not in body and "wget" not in body
    assert "systemctl" not in body


def test_prepare_host_has_bounded_non_activating_scope():
    body = script("prepare-host.sh")
    assert "duplicate release instance UUID" in body
    assert "remote-dev-runner" in body and "remote-dev-gateway" in body
    assert "code-server@.service" in body
    assert "remote-dev-code-server" in body
    assert "locked-settings.json" in body
    assert "remote-dev-submit-0.1.0.vsix" in body
    assert "create_workspace_image" in body
    assert "1024" in body
    assert "4.135.0" in body
    assert "de89acbcdce9d9b870008a270c9f6466993d91f4" in body
    for forbidden in (
        "systemctl start", "systemctl restart", "systemctl enable",
        "docker", "containerd", "nginx", "cloudflared tunnel", "useradd rdp-",
    ):
        assert forbidden not in body.lower()


def test_prepare_host_atomically_publishes_root_owned_runtime_configs():
    body = script("prepare-host.sh")
    common = script("common.sh")
    assert body.count("publish_root_file") >= 3
    assert "mktemp \"$CONFIG_DIR/" in body
    assert "chown root:root" in common
    assert "mv -Tf" in common


def test_bootstrap_wrapper_is_offline_root_only_and_never_accepts_secrets():
    body = script("bootstrap-instance.sh")
    assert "assert_managed_services_inactive" in body
    assert "flock -n 9" in body
    assert '--release "$RELEASE"' in body
    assert "secure_control_database_files" in body
    common = script("common.sh")
    assert "chown remote-dev:remote-dev-runner" in common
    assert "chmod 0600" in common
    assert "bootstrap-instance" in body
    assert "--password" not in body and "--token" not in body
    assert "systemctl start" not in body and "systemctl restart" not in body


def test_verify_derives_exact_roster_from_release_and_checks_credentials():
    body = script("verify.sh")
    assert "--instance" not in body
    assert "remote_dev.verify_bootstrap" in body
    assert '"$CONFIG_DIR/credentials"' in body
    verifier = (ROOT / "src/remote_dev/verify_bootstrap.py").read_text()
    assert "api_token_hash" in verifier and "hmac.compare_digest" in verifier
    assert "mode=ro&immutable=1" in body
    assert "verify_control_database_files" in body
    assert "setpriv --reuid=remote-dev --regid=remote-dev-runner --init-groups" in body
    assert "runuser" not in body


def test_backup_is_atomic_and_uses_sqlite_online_backup_with_integrity_check():
    body = script("backup.sh")
    assert "sqlite3" in body
    assert ".backup" in body
    assert "PRAGMA integrity_check" in body
    assert ".incomplete" in body
    assert "mv" in body
    assert "secure_control_database_files" in body
    assert "--reflink=auto" in body
    assert "/var/lib/remote-dev/workspaces" in body
    assert "/var/lib/private/remote-dev/code-server" in body
    assert "/etc/remote-dev" in body
    assert "manifest.sha256" in body
    assert "state-config.inventory" in body
    assert "assert_workspace_services_inactive" in body
    assert "/etc/cloudflared" not in body


def test_restore_requires_empty_target_or_pre_restore_backup_and_checks_db():
    body = script("restore.sh")
    assert "--empty-target" in body
    assert "--backup-current" in body
    assert "PRAGMA integrity_check" in body
    assert "backup.sh" in body
    assert "systemctl stop" not in body
    assert "systemctl restart" not in body
    assert "secure_control_database_files" in body


def test_rollback_atomically_switches_versioned_release_symlink_only():
    body = script("rollback.sh")
    assert "/opt/remote-dev/releases" in body
    assert "ln -s" in body
    assert "mv -T" in body
    assert "current" in body
    for forbidden in ("docker", "containerd", "nginx", "cloudflared", "systemctl restart"):
        assert forbidden not in body.lower()


def test_scripts_do_not_enable_shell_tracing_or_accept_credentials():
    for name in (*SCRIPT_NAMES, "common.sh"):
        body = script(name)
        assert "set -x" not in body
        assert not re.search(r"(--password|--token|--cookie|--credential)\\b", body, re.I)
    assert "unsupported config entry" in script("backup.sh")


def test_non_root_execution_is_rejected_before_action():
    if os.geteuid() == 0:
        return
    for name in SCRIPT_NAMES:
        result = subprocess.run(
            [str(SCRIPTS / name)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        assert "root" in result.stderr.lower()


def test_workspace_layout_rejects_every_non_uuid_image_entry(tmp_path):
    malformed = tmp_path / "not-a-uuid.txt"
    malformed.write_text("unsafe", encoding="utf-8")
    command = (
        f'source "{SCRIPTS / "common.sh"}"; '
        f'validate_uuid_entries "{tmp_path}" image'
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "invalid" in result.stderr.lower()
    assert 'validate_uuid_entries "$WORKSPACES_DIR" image\n' in script("backup.sh")
    assert 'validate_uuid_entries "$STATE_DIR" state\n' in script("backup.sh")


def test_new_deployment_runbook_replaces_permanent_linux_user_workflow():
    deployment = (ROOT / "docs/deployment.md").read_text(encoding="utf-8")
    assert "DynamicUser" in deployment
    assert "/var/lib/remote-dev/workspaces/<UUID>.img" in deployment
    assert "/var/lib/private/remote-dev/code-server/<UUID>" in deployment
    assert "/run/remote-dev/control-worker.sock" in deployment
    assert "/opt/code-server" in deployment
    assert "永久 Linux 开发用户" in deployment
    assert "禁止" in deployment
    assert "backup.sh" in deployment
    assert "restore.sh" in deployment
    assert "rollback.sh" in deployment
    old = (ROOT / "docs/01-user-required-actions.md").read_text(encoding="utf-8")
    assert "useradd" not in old
    assert "docs/deployment.md" in old
