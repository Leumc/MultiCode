"""Administrative CLI for development and systemd services."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

import uvicorn

from .app import create_app
from .broker import (
    AllowedUidAuthorizer, DatabaseJobBroker, UnixJobBrokerClient, UnixJobBrokerServer,
)
from .cgroup_v2 import CgroupManager
from .constants import (
    COMPILE_MEMORY_MIB, COMPILE_WALL_MS, MAX_SUBMISSION_WALL_MS,
    STDERR_LIMIT_BYTES, STDOUT_LIMIT_BYTES,
)
from .database import Database
from .gateway_config import GatewayUser, render_caddyfile
from .runner import LocalCppRunner, RunnerLimits, SandboxedCppRunner
from .worker import JobWorker


def paths(args):
    data = Path(args.data_dir or os.environ.get("REMOTE_DEV_DATA", "/var/lib/remote-dev"))
    workspaces = Path(args.workspace_root or os.environ.get("REMOTE_DEV_WORKSPACES", "/var/lib/remote-dev/workspaces"))
    return data, workspaces


def cmd_init_admin(args) -> int:
    data, _ = paths(args)
    database = Database(data / "control.db")
    database.initialize()
    if database.get_user_by_username(args.username):
        raise SystemExit(f"administrator {args.username!r} already exists")
    password = args.password or getpass.getpass("Administrator password: ")
    if len(password) < 12:
        raise SystemExit("administrator password must be at least 12 characters")
    database.create_admin(args.username, password)
    print(f"created administrator: {args.username}")
    return 0


def cmd_serve(args) -> int:
    data, workspaces = paths(args)
    database = Database(data / "control.db")
    database.initialize()
    application = create_app(
        database=database, workspace_root=workspaces, secure_cookies=not args.insecure_cookie,
    )
    socket_path = Path(args.broker_socket) if args.broker_socket else data / "control-worker.sock"
    allowed_uids = set(args.worker_uid or [os.getuid()])
    with UnixJobBrokerServer(
        socket_path,
        DatabaseJobBroker(database),
        peer_authorizer=AllowedUidAuthorizer(allowed_uids),
    ):
        uvicorn.run(application, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    return 0


def cmd_worker(args) -> int:
    data, _ = paths(args)
    socket_path = Path(args.broker_socket) if args.broker_socket else data / "control-worker.sock"
    broker = UnixJobBrokerClient(socket_path)
    limits = RunnerLimits(
        compile_wall_ms=COMPILE_WALL_MS,
        compile_memory_mib=COMPILE_MEMORY_MIB,
        stdout_limit_bytes=STDOUT_LIMIT_BYTES,
        stderr_limit_bytes=STDERR_LIMIT_BYTES,
        total_wall_ms=MAX_SUBMISSION_WALL_MS,
    )
    if getattr(args, "unsafe_local_runner", False):
        runner = LocalCppRunner(data / "runner", limits)
    else:
        cgroup_manager = CgroupManager.discover()
        cgroup_manager.activate()
        runner = SandboxedCppRunner(
            data / "runner", limits,
            sandbox_exec=getattr(
                args, "sandbox_exec", "/usr/libexec/remote-dev/sandbox-exec"
            ),
            cgroup_manager=cgroup_manager,
        )
    worker = JobWorker(broker, runner)
    worker.run_forever(args.poll_interval)
    return 0


def cmd_render_gateway(args) -> int:
    users: list[GatewayUser] = []
    for value in args.user:
        try:
            public_id, raw_port = value.rsplit(":", 1)
            users.append(GatewayUser(public_id, int(raw_port)))
        except (ValueError, TypeError) as exc:
            raise SystemExit(f"invalid --user {value!r}; expected UUID:port") from exc
    content = render_caddyfile(users)
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
    else:
        print(content, end="")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="remote-dev")
    root.add_argument("--data-dir")
    root.add_argument("--workspace-root")
    commands = root.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init-admin")
    init.add_argument("--username", default="admin")
    init.add_argument("--password", help="prefer omitting this so getpass does not expose it")
    init.set_defaults(handler=cmd_init_admin)

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=9000)
    serve.add_argument("--insecure-cookie", action="store_true", help="development loopback only")
    serve.add_argument("--broker-socket", help="control-worker Unix socket path")
    serve.add_argument(
        "--worker-uid", action="append", type=int,
        help="UID authorized on the worker socket (repeatable; defaults to control UID)",
    )
    serve.set_defaults(handler=cmd_serve)

    worker = commands.add_parser("worker")
    worker.add_argument("--poll-interval", type=float, default=0.25)
    worker.add_argument("--broker-socket", help="control-worker Unix socket path")
    worker.add_argument(
        "--sandbox-exec", default="/usr/libexec/remote-dev/sandbox-exec",
        help="absolute path to the reviewed seccomp launcher",
    )
    worker.add_argument(
        "--unsafe-local-runner", action="store_true",
        help="development tests only: run code without bubblewrap/seccomp",
    )
    worker.set_defaults(handler=cmd_worker)

    gateway = commands.add_parser("render-gateway")
    gateway.add_argument("--user", action="append", default=[], metavar="UUID:PORT")
    gateway.add_argument("--output")
    gateway.set_defaults(handler=cmd_render_gateway)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
