import json
import argparse
import socket
from pathlib import Path
from dataclasses import asdict
from typing import get_type_hints

import pytest

from remote_dev.broker import (
    DEFAULT_MAX_MESSAGE_BYTES,
    DatabaseJobBroker,
    PeerIdentity,
    UnixJobBrokerClient,
    UnixJobBrokerServer,
)
from remote_dev.database import Database
from remote_dev.runner import CaseResult, CompileResult, ExecutionResult
from remote_dev.worker import JobWorker
from remote_dev import cli


def request(socket_path, payload: bytes) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(socket_path))
        try:
            client.sendall(payload + b"\n")
        except BrokenPipeError:
            pass  # A peer denial may race with the request write after sending its response.
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
    return json.loads(response)


@pytest.fixture
def database(tmp_path):
    value = Database(tmp_path / "control.db")
    value.initialize()
    return value


@pytest.fixture
def server(tmp_path, database):
    value = UnixJobBrokerServer(
        tmp_path / "control-worker.sock",
        DatabaseJobBroker(database),
        peer_authorizer=lambda peer: True,
    )
    value.start()
    try:
        yield value
    finally:
        value.close()


def create_job(database):
    database.create_admin("admin", "correct horse battery staple")
    user = database.create_developer(
        "alice", "developer passphrase 123", "token", "/tmp/alice"
    )
    return database.create_job(user["id"], {
        "filename": "main.cpp",
        "source": "int main(){}",
        "compiler": "gcc-14-gnu++17",
        "time_limit_ms": 1000,
        "memory_limit_mib": 64,
        "inputs": ["hello\n"],
    })["id"]


def test_client_round_trips_only_worker_operations(server, database):
    job_id = create_job(database)
    client = UnixJobBrokerClient(server.socket_path)

    claimed = client.claim_next_job()
    assert claimed["id"] == job_id
    assert claimed["owner_public_id"] == database.get_user_by_username("alice")["public_id"]
    assert claimed["cases"][0]["input_text"] == "hello\n"
    assert client.is_cancel_requested(job_id) is False

    execution = ExecutionResult(
        "completed",
        CompileResult("completed", stdout="compiled", exit_code=0, wall_time_ms=4),
        [CaseResult(1, "completed", stdout="ok", exit_code=0, wall_time_ms=2)],
    )
    client.finish_job(job_id, execution)
    assert database.get_job(job_id)["status"] == "completed"
    assert database.get_job(job_id)["cases"][0]["stdout"] == "ok"

    second_id = database.create_job(database.get_user_by_username("alice")["id"], {
        "filename": "other.cpp", "source": "int main(){}",
        "compiler": "gcc-14-gnu++17", "time_limit_ms": 1000,
        "memory_limit_mib": 64, "inputs": [""],
    })["id"]
    assert client.claim_next_job()["id"] == second_id
    client.mark_job_system_error(second_id, "runner exploded")
    assert database.get_job(second_id)["status"] == "system_error"


@pytest.mark.parametrize("payload", [
    {"op": "delete", "job_id": 1},
    {"op": "claim", "extra": True},
    {"op": "cancel-check", "job_id": True},
    {"op": "complete", "job_id": 1, "execution": {}, "extra": 1},
    {"op": "system-error", "job_id": 0, "message": "bad"},
])
def test_server_rejects_unknown_operations_extra_fields_and_wrong_types(server, payload):
    response = request(server.socket_path, json.dumps(payload).encode())
    assert response["ok"] is False
    assert response["error"] == "invalid_request"


def test_server_rejects_malformed_json(server):
    assert request(server.socket_path, b"not-json") == {
        "ok": False, "error": "invalid_request"
    }


def test_server_rejects_messages_over_configured_limit(tmp_path, database):
    limited = UnixJobBrokerServer(
        tmp_path / "small.sock",
        DatabaseJobBroker(database),
        max_message_bytes=64,
        peer_authorizer=lambda peer: True,
    )
    limited.start()
    try:
        response = request(limited.socket_path, b"{" + b" " * 64 + b"}")
        assert response == {"ok": False, "error": "message_too_large"}
    finally:
        limited.close()


def test_message_limit_counts_json_payload_but_not_line_delimiter(tmp_path, database):
    limited = UnixJobBrokerServer(
        tmp_path / "boundary.sock", DatabaseJobBroker(database),
        max_message_bytes=64, peer_authorizer=lambda peer: True,
    )
    limited.start()
    try:
        response = request(limited.socket_path, b" " * 64)
        assert response == {"ok": False, "error": "invalid_request"}
    finally:
        limited.close()


def test_server_rejects_unknown_nested_execution_status(server):
    execution = ExecutionResult("made-up", CompileResult("completed"), [])
    response = request(server.socket_path, json.dumps({
        "op": "complete", "job_id": 1, "execution": asdict(execution),
    }).encode())
    assert response == {"ok": False, "error": "invalid_request"}


def test_peer_authorizer_receives_testable_identity_and_can_deny(tmp_path, database):
    seen = []

    def deny(peer):
        seen.append(peer)
        return False

    value = UnixJobBrokerServer(
        tmp_path / "denied.sock",
        DatabaseJobBroker(database),
        peer_authorizer=deny,
        peer_identity_provider=lambda connection: PeerIdentity(pid=11, uid=22, gid=33),
    )
    value.start()
    try:
        assert request(value.socket_path, b'{"op":"claim"}') == {
            "ok": False, "error": "unauthorized_peer"
        }
    finally:
        value.close()
    assert seen == [PeerIdentity(pid=11, uid=22, gid=33)]


def test_default_message_limit_has_a_finite_documented_ceiling():
    assert DEFAULT_MAX_MESSAGE_BYTES == 32 * 1024 * 1024


def test_worker_depends_on_job_broker_protocol():
    annotation = get_type_hints(JobWorker.__init__)["broker"]
    assert annotation.__name__ == "JobBroker"


def test_worker_cli_uses_socket_client_and_never_opens_database(tmp_path, monkeypatch):
    seen = {}

    class StopLoop(Exception):
        pass

    class FakeClient:
        def __init__(self, path):
            seen["socket_path"] = path

    class FakeWorker:
        def __init__(self, broker, runner):
            seen["broker"] = broker

        def run_forever(self, poll_interval):
            seen["poll_interval"] = poll_interval
            raise StopLoop

    monkeypatch.setattr(cli, "UnixJobBrokerClient", FakeClient)
    monkeypatch.setattr(cli, "JobWorker", FakeWorker)
    monkeypatch.setattr(cli, "Database", lambda path: pytest.fail("worker opened control.db"))
    args = argparse.Namespace(
        data_dir=str(tmp_path), workspace_root=None, poll_interval=0,
        broker_socket=None, unsafe_local_runner=True,
    )
    with pytest.raises(StopLoop):
        cli.cmd_worker(args)
    assert seen["socket_path"] == tmp_path / "control-worker.sock"
    assert isinstance(seen["broker"], FakeClient)
    assert seen["poll_interval"] == 0


def test_worker_cli_defaults_to_production_sandbox(tmp_path, monkeypatch):
    seen = {}

    class StopLoop(Exception):
        pass

    class FakeSandbox:
        def __init__(self, work_root, limits, *, sandbox_exec, cgroup_manager):
            seen["work_root"] = work_root
            seen["sandbox_exec"] = sandbox_exec
            seen["cgroup_manager"] = cgroup_manager

    class FakeCgroup:
        @classmethod
        def discover(cls):
            seen["cgroup_discovered"] = True
            return cls()
        def activate(self):
            seen["cgroup_activated"] = True

    class FakeWorker:
        def __init__(self, broker, runner):
            seen["runner"] = runner

        def run_forever(self, poll_interval):
            seen["poll_interval"] = poll_interval
            raise StopLoop

    monkeypatch.setattr(cli, "UnixJobBrokerClient", lambda path: object())
    monkeypatch.setattr(cli, "SandboxedCppRunner", FakeSandbox, raising=False)
    monkeypatch.setattr(cli, "CgroupManager", FakeCgroup)
    monkeypatch.setattr(cli, "LocalCppRunner", lambda *args, **kwargs: pytest.fail("unsafe runner selected"))
    monkeypatch.setattr(cli, "JobWorker", FakeWorker)
    args = argparse.Namespace(
        data_dir=str(tmp_path), workspace_root=None, poll_interval=0,
        broker_socket=None, unsafe_local_runner=False,
        sandbox_exec="/custom/sandbox-exec",
    )
    with pytest.raises(StopLoop):
        cli.cmd_worker(args)
    assert isinstance(seen["runner"], FakeSandbox)
    assert seen["work_root"] == tmp_path / "runner"
    assert seen["sandbox_exec"] == "/custom/sandbox-exec"
    assert seen["cgroup_discovered"] is True
    assert seen["cgroup_activated"] is True
    assert isinstance(seen["cgroup_manager"], FakeCgroup)


def test_cli_defaults_use_uuid_workspace_layout(monkeypatch):
    monkeypatch.delenv("REMOTE_DEV_DATA", raising=False)
    monkeypatch.delenv("REMOTE_DEV_WORKSPACES", raising=False)
    args = argparse.Namespace(data_dir=None, workspace_root=None)
    assert cli.paths(args)[1] == Path("/var/lib/remote-dev/workspaces")

    parser = cli.parser()
    gateway = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ).choices["render-gateway"]
    assert "UUID:PORT" in gateway.format_help()


def test_control_cli_owns_broker_socket_for_serve_lifetime(tmp_path, monkeypatch):
    seen = {}

    def fake_run(application, **kwargs):
        socket_path = tmp_path / "custom.sock"
        seen["exists_during_run"] = socket_path.is_socket()

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    args = argparse.Namespace(
        data_dir=str(tmp_path), workspace_root=str(tmp_path / "ws"),
        insecure_cookie=True, host="127.0.0.1", port=9000,
        broker_socket=str(tmp_path / "custom.sock"), worker_uid=[1234],
    )
    assert cli.cmd_serve(args) == 0
    assert seen["exists_during_run"] is True
    assert not (tmp_path / "custom.sock").exists()
