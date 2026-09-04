"""Narrow control/worker broker over a length-limited Unix domain socket."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import threading
from typing import Any, Callable, Protocol, runtime_checkable
import uuid

from .constants import TERMINAL_STATES
from .runner import CaseResult, CompileResult, ExecutionResult

DEFAULT_MAX_MESSAGE_BYTES = 32 * 1024 * 1024
_MAX_ERROR_MESSAGE_CHARS = 16_384


class BrokerProtocolError(RuntimeError):
    """The peer returned a response outside the broker contract."""


@runtime_checkable
class JobBroker(Protocol):
    """Only persistence capabilities available to a worker."""

    def claim_next_job(self) -> dict[str, Any] | None: ...

    def is_cancel_requested(self, job_id: int) -> bool: ...

    def finish_job(self, job_id: int, execution: ExecutionResult) -> None: ...

    def mark_job_system_error(self, job_id: int, message: str) -> None: ...


class DatabaseJobBroker:
    """In-process adapter retained for tests; production workers use the UDS client."""

    def __init__(self, database: Any):
        self._database = database

    def claim_next_job(self) -> dict[str, Any] | None:
        return self._database.claim_next_job()

    def is_cancel_requested(self, job_id: int) -> bool:
        return self._database.is_cancel_requested(job_id)

    def finish_job(self, job_id: int, execution: ExecutionResult) -> None:
        self._database.finish_job(job_id, execution)

    def mark_job_system_error(self, job_id: int, message: str) -> None:
        self._database.mark_job_system_error(job_id, message)


@dataclass(frozen=True)
class PeerIdentity:
    pid: int
    uid: int
    gid: int


PeerIdentityProvider = Callable[[socket.socket], PeerIdentity]
PeerAuthorizer = Callable[[PeerIdentity], bool]


def linux_peer_identity(connection: socket.socket) -> PeerIdentity:
    """Read kernel-authenticated Unix peer credentials (Linux only)."""
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return PeerIdentity(*struct.unpack("3i", raw))


class AllowedUidAuthorizer:
    def __init__(self, allowed_uids: set[int]):
        self._allowed_uids = frozenset(allowed_uids)

    def __call__(self, peer: PeerIdentity) -> bool:
        return peer.uid in self._allowed_uids


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _exact_object(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("object keys do not match schema")
    return value


def _positive_job_id(value: Any) -> int:
    if not _is_int(value) or value <= 0:
        raise ValueError("job_id must be a positive integer")
    return value


def _nullable_int(value: Any) -> int | None:
    if value is not None and (not _is_int(value) or value < 0):
        raise ValueError("expected a non-negative integer or null")
    return value


def _canonical_uuid(value: Any) -> str:
    text = _text(value)
    try:
        parsed = str(uuid.UUID(text))
    except ValueError as error:
        raise ValueError("expected canonical UUID") from error
    if parsed != text:
        raise ValueError("expected canonical UUID")
    return text


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _one_of(value: Any, allowed: set[str]) -> str:
    text = _text(value)
    if text not in allowed:
        raise ValueError("value is outside the allowed enum")
    return text


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("expected boolean")
    return value


def _execution_to_wire(execution: ExecutionResult) -> dict[str, Any]:
    return execution.to_dict()


def _execution_from_wire(value: Any) -> ExecutionResult:
    data = _exact_object(value, {"status", "compile", "cases"})
    status = _one_of(data["status"], TERMINAL_STATES)
    compile_data = _exact_object(data["compile"], {
        "status", "stdout", "stderr", "exit_code", "wall_time_ms", "output_truncated"
    })
    compile_result = CompileResult(
        status=_one_of(compile_data["status"], {"completed", "compile_error", "system_error"}),
        stdout=_text(compile_data["stdout"]),
        stderr=_text(compile_data["stderr"]),
        exit_code=_nullable_int(compile_data["exit_code"]),
        wall_time_ms=_nullable_int(compile_data["wall_time_ms"]),
        output_truncated=_boolean(compile_data["output_truncated"]),
    )
    if not isinstance(data["cases"], list):
        raise ValueError("cases must be an array")
    cases: list[CaseResult] = []
    case_keys = {
        "position", "status", "stdout", "stderr", "exit_code", "term_signal",
        "wall_time_ms", "peak_memory_kib", "output_truncated",
    }
    for raw_case in data["cases"]:
        case = _exact_object(raw_case, case_keys)
        position = _positive_job_id(case["position"])
        cases.append(CaseResult(
            position=position,
            status=_one_of(case["status"], TERMINAL_STATES),
            stdout=_text(case["stdout"]),
            stderr=_text(case["stderr"]),
            exit_code=_nullable_int(case["exit_code"]),
            term_signal=_nullable_int(case["term_signal"]),
            wall_time_ms=_nullable_int(case["wall_time_ms"]),
            peak_memory_kib=_nullable_int(case["peak_memory_kib"]),
            output_truncated=_boolean(case["output_truncated"]),
        ))
    return ExecutionResult(status, compile_result, cases)


def _job_to_wire(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "id": job["id"],
        "owner_public_id": job["owner_public_id"],
        "filename": job["filename"],
        "source": job["source"],
        "compiler": job["compiler"],
        "time_limit_ms": job["time_limit_ms"],
        "memory_limit_mib": job["memory_limit_mib"],
        "cases": [{"input_text": case["input_text"]} for case in job["cases"]],
    }


def _job_from_wire(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    data = _exact_object(value, {
        "id", "owner_public_id", "filename", "source", "compiler", "time_limit_ms", "memory_limit_mib", "cases"
    })
    result = {
        "id": _positive_job_id(data["id"]),
        "owner_public_id": _canonical_uuid(data["owner_public_id"]),
        "filename": _text(data["filename"]),
        "source": _text(data["source"]),
        "compiler": _text(data["compiler"]),
        "time_limit_ms": _positive_job_id(data["time_limit_ms"]),
        "memory_limit_mib": _positive_job_id(data["memory_limit_mib"]),
    }
    if not isinstance(data["cases"], list):
        raise ValueError("cases must be an array")
    result["cases"] = [
        {"input_text": _text(_exact_object(case, {"input_text"})["input_text"])}
        for case in data["cases"]
    ]
    return result


def _parse_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("op"), str):
        raise ValueError("request must have an operation")
    operation = value["op"]
    expected = {
        "claim": {"op"},
        "cancel-check": {"op", "job_id"},
        "complete": {"op", "job_id", "execution"},
        "system-error": {"op", "job_id", "message"},
    }.get(operation)
    if expected is None:
        raise ValueError("unknown operation")
    data = _exact_object(value, expected)
    if "job_id" in data:
        _positive_job_id(data["job_id"])
    if operation == "complete":
        _execution_from_wire(data["execution"])
    if operation == "system-error":
        message = _text(data["message"])
        if len(message) > _MAX_ERROR_MESSAGE_CHARS:
            raise ValueError("message too long")
    return data


def _dispatch(broker: JobBroker, request: dict[str, Any]) -> dict[str, Any]:
    operation = request["op"]
    if operation == "claim":
        return {"ok": True, "job": _job_to_wire(broker.claim_next_job())}
    if operation == "cancel-check":
        return {"ok": True, "cancel_requested": broker.is_cancel_requested(request["job_id"])}
    if operation == "complete":
        broker.finish_job(request["job_id"], _execution_from_wire(request["execution"]))
        return {"ok": True}
    broker.mark_job_system_error(request["job_id"], request["message"])
    return {"ok": True}


def _encode_message(value: dict[str, Any], maximum: int) -> bytes:
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    if len(encoded) - 1 > maximum:
        raise BrokerProtocolError("message exceeds configured limit")
    return encoded


class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class _RequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        owner: UnixJobBrokerServer = self.server.owner  # type: ignore[attr-defined]
        try:
            peer = owner.peer_identity_provider(self.request)
            if not owner.peer_authorizer(peer):
                self._reply({"ok": False, "error": "unauthorized_peer"})
                return
            raw = bytearray()
            while True:
                chunk = self.request.recv(min(65_536, owner.max_message_bytes + 1 - len(raw)))
                if not chunk:
                    return
                raw.extend(chunk)
                if b"\n" in chunk:
                    break
                if len(raw) > owner.max_message_bytes:
                    self._reply({"ok": False, "error": "message_too_large"})
                    return
            line, separator, remainder = bytes(raw).partition(b"\n")
            if len(line) > owner.max_message_bytes:
                self._reply({"ok": False, "error": "message_too_large"})
                return
            if not separator or remainder:
                raise ValueError("exactly one JSON line is required")
            request = _parse_request(json.loads(line.decode("utf-8")))
            self._reply(_dispatch(owner.broker, request))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._reply({"ok": False, "error": "invalid_request"})
        except Exception:
            self._reply({"ok": False, "error": "internal_error"})

    def _reply(self, value: dict[str, Any]) -> None:
        owner: UnixJobBrokerServer = self.server.owner  # type: ignore[attr-defined]
        try:
            self.request.sendall(_encode_message(value, owner.max_message_bytes))
        except (BrokenPipeError, BrokerProtocolError):
            pass


class UnixJobBrokerServer:
    def __init__(
        self,
        socket_path: str | Path,
        broker: JobBroker,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        peer_authorizer: PeerAuthorizer | None = None,
        peer_identity_provider: PeerIdentityProvider = linux_peer_identity,
    ):
        if max_message_bytes < 64:
            raise ValueError("max_message_bytes must be at least 64")
        self.socket_path = Path(socket_path)
        self.broker = broker
        self.max_message_bytes = max_message_bytes
        self.peer_authorizer = peer_authorizer or AllowedUidAuthorizer({os.getuid()})
        self.peer_identity_provider = peer_identity_provider
        self._server: _ThreadingUnixServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("broker server already started")
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            mode = self.socket_path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise RuntimeError(f"refusing to replace non-socket path: {self.socket_path}")
            self.socket_path.unlink()
        server = _ThreadingUnixServer(str(self.socket_path), _RequestHandler)
        server.owner = self  # type: ignore[attr-defined]
        os.chmod(self.socket_path, 0o660)
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="job-broker", daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> UnixJobBrokerServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class UnixJobBrokerClient:
    def __init__(
        self,
        socket_path: str | Path,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        timeout_seconds: float = 10.0,
    ):
        self.socket_path = Path(socket_path)
        self.max_message_bytes = max_message_bytes
        self.timeout_seconds = timeout_seconds

    def _call(self, request: dict[str, Any]) -> dict[str, Any]:
        outgoing = _encode_message(request, self.max_message_bytes)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout_seconds)
            connection.connect(str(self.socket_path))
            connection.sendall(outgoing)
            raw = bytearray()
            while True:
                chunk = connection.recv(min(65_536, self.max_message_bytes + 1 - len(raw)))
                if not chunk:
                    raise BrokerProtocolError("broker closed before a response")
                raw.extend(chunk)
                if b"\n" in chunk:
                    break
                if len(raw) > self.max_message_bytes:
                    raise BrokerProtocolError("broker response exceeds configured limit")
        line, separator, remainder = bytes(raw).partition(b"\n")
        if len(line) > self.max_message_bytes:
            raise BrokerProtocolError("broker response exceeds configured limit")
        if not separator or remainder:
            raise BrokerProtocolError("broker response is not exactly one JSON line")
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BrokerProtocolError("broker response is invalid JSON") from error
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise BrokerProtocolError("broker response violates schema")
        if response["ok"] is False:
            if set(response) != {"ok", "error"} or not isinstance(response["error"], str):
                raise BrokerProtocolError("broker error response violates schema")
            raise BrokerProtocolError(f"broker rejected request: {response['error']}")
        return response

    def claim_next_job(self) -> dict[str, Any] | None:
        response = self._call({"op": "claim"})
        _exact_object(response, {"ok", "job"})
        return _job_from_wire(response["job"])

    def is_cancel_requested(self, job_id: int) -> bool:
        response = self._call({"op": "cancel-check", "job_id": job_id})
        _exact_object(response, {"ok", "cancel_requested"})
        return _boolean(response["cancel_requested"])

    def finish_job(self, job_id: int, execution: ExecutionResult) -> None:
        response = self._call({
            "op": "complete", "job_id": job_id, "execution": _execution_to_wire(execution)
        })
        _exact_object(response, {"ok"})

    def mark_job_system_error(self, job_id: int, message: str) -> None:
        response = self._call({"op": "system-error", "job_id": job_id, "message": message})
        _exact_object(response, {"ok"})
