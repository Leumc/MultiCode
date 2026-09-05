from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "deploy/apparmor/remote-dev-code-server"


def profile_text() -> str:
    return PROFILE.read_text(encoding="utf-8")


def test_profile_allows_dynamic_user_private_state_and_own_cgroup_metadata():
    body = profile_text()
    assert "owner /var/lib/private/remote-dev/code-server/* rwk," in body
    assert "owner /var/lib/private/remote-dev/code-server/*/** rwk," in body
    assert "owner /var/cache/private/remote-dev/code-server/* rwk," in body
    assert "owner /var/cache/private/remote-dev/code-server/*/** rwk," in body
    assert "owner /proc/*/cgroup r," in body


def test_profile_uses_uuid_image_mount_layout_not_legacy_srv_paths():
    value = profile_text()
    assert "/srv/remote-dev" not in value
    assert "/opt/remote-dev/current/code-server/lockdown-spawn.cjs r," in value
    assert "/opt/remote-dev/releases/*/code-server/lockdown-spawn.cjs r," in value
    assert "/run/credentials/code-server@*.service/remote-dev-api-token r," in value
    assert "/opt/remote-dev/lockdown-spawn.cjs" not in value
    assert "owner /workspace/** rwk," in value
    assert "deny /workspace/** x," in value
    assert "owner /var/lib/remote-dev/code-server/*/** rwk," in value
    assert "owner /var/cache/remote-dev/code-server/*/** rwk," in value
    assert "owner /run/remote-dev/code-server/*/** rwk," in value


def test_profile_never_allows_shell_or_compiler_execution():
    value = profile_text()
    assert "/opt/code-server/lib/node ix," in value
    assert "/usr/bin/clangd-19 ixr," in value
    assert "deny /opt/code-server/lib/vscode/node_modules/node-pty/build/Release/pty.node mr," in value
    assert "deny /bin/** x," in value
    assert "deny /usr/bin/{sh,bash,dash,zsh,git,gdb,lldb,g++,gcc,cc,c++,make,cmake,python*,perl,ruby}* x," in value


def test_profile_parses_offline_without_writing_system_cache():
    result = subprocess.run(
        ["/usr/sbin/apparmor_parser", "-Q", "-K", str(PROFILE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
