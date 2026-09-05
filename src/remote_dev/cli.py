"""Administrative CLI for development and systemd services."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import uuid

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
from .security import new_api_token, token_hash
from .worker import JobWorker


def paths(args):
    data = Path(args.data_dir or os.environ.get("REMOTE_DEV_DATA", "/var/lib/remote-dev"))
    workspaces = Path(args.workspace_root or os.environ.get("REMOTE_DEV_WORKSPACES", "/var/lib/remote-dev/workspaces"))
    return data, workspaces


def cmd_init_admin(args) -> int:
    previous_umask = os.umask(0o077)
    try:
        data, _ = paths(args)
        database = Database(data / "control.db")
        database.initialize()
        if database.get_user_by_username(args.username):
            raise SystemExit(f"administrator {args.username!r} already exists")
        password = getpass.getpass("Administrator password: ")
        if len(password) < 12:
            raise SystemExit("administrator password must be at least 12 characters")
        database.create_admin(args.username, password)
        print(f"created administrator: {args.username}")
        return 0
    finally:
        os.umask(previous_umask)


def _release_instances(release: Path) -> list[str]:
    try:
        instances = release.joinpath("instances").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit("unable to read release instance roster") from exc
    if not 1 <= len(instances) <= 3 or len(set(instances)) != len(instances):
        raise SystemExit("release instance roster must contain one to three unique UUIDs")
    for value in instances:
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            raise SystemExit("release instance roster contains a noncanonical UUID") from None
    return instances


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_safe_bootstrap_credential(path: Path) -> str:
    try:
        details = path.lstat()
    except OSError as exc:
        raise SystemExit("developer bootstrap credential is unreadable") from exc
    if (
        path.is_symlink() or not path.is_file()
        or details.st_uid != os.geteuid()
        or (details.st_mode & 0o777) not in {0o400, 0o600}
    ):
        raise SystemExit("developer bootstrap credential is unsafe")
    try:
        token = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit("developer bootstrap credential is unreadable") from exc
    if not token:
        raise SystemExit("developer bootstrap credential is empty")
    return token


def bootstrap_instance(
    *, database: Database, release: Path, workspace_root: Path,
    credential_dir: Path, public_id: str, username: str, password: str,
    token_factory=new_api_token,
) -> dict:
    if public_id not in _release_instances(release):
        raise SystemExit("instance UUID is not declared by the current release")
    if len(password) < 12:
        raise SystemExit("developer password must be at least 12 characters")
    workspace = workspace_root / f"{public_id}.img"
    if not workspace.is_file() or workspace.is_symlink():
        raise SystemExit("prepared workspace image is missing or unsafe")
    credential = credential_dir / f"{public_id}.token"
    existing_public = database.get_user_by_public_id(public_id)
    existing_username = database.get_user_by_username(username)
    staged = list(credential_dir.glob(f".{public_id}.token.*"))
    if existing_public or existing_username:
        if (
            not existing_public or not existing_username
            or existing_public["id"] != existing_username["id"]
            or existing_public["workspace_path"] != str(workspace)
        ):
            raise SystemExit("developer is already configured inconsistently")
        if len(staged) > 1:
            raise SystemExit("developer bootstrap recovery state is ambiguous")
        expected_hash = existing_public["api_token_hash"]
        if credential.exists() or credential.is_symlink():
            if token_hash(_read_safe_bootstrap_credential(credential)) != expected_hash:
                raise SystemExit("developer bootstrap credential does not match database")
            if staged:
                if token_hash(_read_safe_bootstrap_credential(staged[0])) != expected_hash:
                    raise SystemExit("developer bootstrap recovery credential does not match database")
                staged[0].unlink()
                _fsync_directory(credential_dir)
            else:
                _fsync_directory(credential_dir)
            return existing_public
        if len(staged) != 1:
            raise SystemExit("developer bootstrap recovery state is ambiguous")
        staged_path = staged[0]
        if token_hash(_read_safe_bootstrap_credential(staged_path)) != expected_hash:
            raise SystemExit("developer bootstrap recovery credential does not match database")
        os.link(staged_path, credential, follow_symlinks=False)
        staged_path.unlink()
        _fsync_directory(credential_dir)
        return existing_public

    if credential.exists() or credential.is_symlink():
        raise SystemExit("instance credential already exists without matching database state")
    if staged:
        if len(staged) != 1:
            raise SystemExit("orphaned developer bootstrap staging state is ambiguous")
        _read_safe_bootstrap_credential(staged[0])
        staged[0].unlink()
        _fsync_directory(credential_dir)

    token = token_factory()
    temporary = credential_dir / f".{public_id}.token.{os.getpid()}"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
    )
    preserve_temporary = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(credential_dir)
        user = database.create_developer(
            username, password, token, str(workspace), public_id=public_id,
        )
        try:
            os.link(temporary, credential, follow_symlinks=False)
            temporary.unlink()
        except Exception:
            try:
                database.delete_unconfigured_developer(user["id"], public_id)
            except BaseException as compensation_error:
                preserve_temporary = True
                raise RuntimeError(
                    "credential publish and database compensation failed; recoverable staging was preserved"
                ) from compensation_error
            raise
        _fsync_directory(credential_dir)
        return user
    finally:
        if not preserve_temporary:
            temporary.unlink(missing_ok=True)


def cmd_bootstrap_instance(args) -> int:
    data, workspaces = paths(args)
    password = getpass.getpass("Developer password: ")
    database = Database(data / "control.db")
    database.initialize()
    user = bootstrap_instance(
        database=database, release=Path(args.release), workspace_root=workspaces,
        credential_dir=Path(args.credential_dir), public_id=args.instance,
        username=args.username, password=password,
    )
    print(f"bootstrapped developer instance: {user['public_id']}")
    return 0


def cmd_serve(args) -> int:
    data, workspaces = paths(args)
    database = Database(data / "control.db")
    database.initialize()
    application = create_app(
        database=database, workspace_root=workspaces, secure_cookies=not args.insecure_cookie,
        managed_mode=True,
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
    init.set_defaults(handler=cmd_init_admin)

    bootstrap = commands.add_parser("bootstrap-instance")
    bootstrap.add_argument("--instance", required=True)
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--release", default="/opt/remote-dev/current")
    bootstrap.add_argument("--credential-dir", default="/etc/remote-dev/credentials")
    bootstrap.set_defaults(handler=cmd_bootstrap_instance)

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
