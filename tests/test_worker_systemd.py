from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "deploy/systemd/remote-dev-worker.service"
CONTROL = ROOT / "deploy/systemd/remote-dev-control.service"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_services_execute_from_atomic_current_release():
    control = text(CONTROL)
    worker = text(WORKER)
    expected = "/opt/remote-dev/current/app/.venv/bin/python -m remote_dev.cli"
    assert f"ExecStart={expected} serve" in control
    assert f"ExecStart={expected} worker" in worker
    assert "WorkingDirectory=/opt/remote-dev/current/app" in control
    assert "WorkingDirectory=/opt/remote-dev/current/app" in worker
    assert "/opt/remote-dev/app" not in control + worker


def test_worker_uses_only_broker_and_production_runner_paths():
    unit = text(WORKER)
    assert "--broker-socket /run/remote-dev/control-worker.sock" in unit
    assert "--unsafe-local-runner" not in unit
    assert "/opt/remote-dev/current/libexec/sandbox-exec" in unit


def test_worker_cannot_access_control_database_or_workspace_images():
    unit = text(WORKER)
    read_write = next(line for line in unit.splitlines() if line.startswith("ReadWritePaths="))
    assert read_write == "ReadWritePaths=/var/lib/remote-dev/runner"
    assert "InaccessiblePaths=-/var/lib/remote-dev/control.db" in unit
    assert "InaccessiblePaths=-/var/lib/remote-dev/workspaces" in unit
    assert "REMOTE_DEV_WORKSPACES" not in unit


def test_control_owns_database_workspace_images_and_broker_runtime():
    unit = text(CONTROL)
    assert "Environment=REMOTE_DEV_WORKSPACES=/var/lib/remote-dev/workspaces" in unit
    assert "RuntimeDirectory=remote-dev" in unit
    assert "--broker-socket /run/remote-dev/control-worker.sock" in unit
    assert "--worker-uid ${REMOTE_DEV_WORKER_UID}" in unit


def test_worker_network_and_cgroup_surface_is_narrow():
    unit = text(WORKER)
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "Delegate=memory pids" in unit
    assert "IPAddressDeny=any" in unit
