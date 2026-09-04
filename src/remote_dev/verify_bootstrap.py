"""Fail-closed verification for a fully bootstrapped managed release."""

from __future__ import annotations

import argparse
import hmac
import os
import sqlite3
import stat
import uuid
from pathlib import Path

from .database import Database
from .gateway_config import GatewayUser, render_caddyfile
from .security import token_hash


def _instances(release: Path) -> list[str]:
    values = (release / "instances").read_text(encoding="utf-8").splitlines()
    if not 1 <= len(values) <= 3 or len(values) != len(set(values)):
        raise ValueError("release roster must contain one to three unique instances")
    for value in values:
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("release roster has a noncanonical UUID") from None
    return values


def _regular_file(path: Path, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError:
        raise ValueError(f"missing {label}: {path.name}") from None
    if not stat.S_ISREG(details.st_mode) or path.is_symlink():
        raise ValueError(f"unsafe {label}: {path.name}")
    return details


def _entry_set(directory: Path) -> set[str]:
    try:
        return {entry.name for entry in directory.iterdir()}
    except OSError as exc:
        raise ValueError(f"unable to enumerate {directory}") from exc


def verify_bootstrap(
    *, database: Database, release: Path, workspace_root: Path, env_dir: Path,
    credential_dir: Path, caddyfile: Path, credential_owner_uid: int = 0,
    config_owner_uid: int = 0,
) -> dict[str, object]:
    instances = _instances(release)
    expected = set(instances)
    database_uri = f"file:{database.path.resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            "SELECT public_id,workspace_path,api_token_hash FROM users WHERE role='developer'"
        )]
    if {row["public_id"] for row in rows} != expected:
        raise ValueError("database developer roster differs from release roster")
    by_id = {row["public_id"]: row for row in rows}

    expected_images = {f"{value}.img" for value in instances}
    expected_envs = {
        "locked-settings.json", "locked-keybindings.json",
        *(f"{value}.env" for value in instances),
    }
    expected_credentials = {f"{value}.token" for value in instances}
    if _entry_set(workspace_root) != expected_images:
        raise ValueError("workspace image roster differs from release roster")
    if _entry_set(env_dir) != expected_envs:
        raise ValueError("instance environment roster differs from release roster")
    if _entry_set(credential_dir) != expected_credentials:
        raise ValueError("credential roster differs from release roster")

    for name in ("locked-settings.json", "locked-keybindings.json"):
        details = _regular_file(env_dir / name, "locked configuration")
        if details.st_uid != config_owner_uid or stat.S_IMODE(details.st_mode) != 0o644:
            raise ValueError(f"unsafe locked configuration ownership or mode: {name}")

    users: list[GatewayUser] = []
    ports: list[int] = []
    for offset, public_id in enumerate(instances):
        workspace = workspace_root / f"{public_id}.img"
        _regular_file(workspace, "workspace image")
        if by_id[public_id]["workspace_path"] != str(workspace):
            raise ValueError(f"workspace path mismatch for {public_id}")

        env_file = env_dir / f"{public_id}.env"
        env_details = _regular_file(env_file, "instance environment")
        if env_details.st_uid != config_owner_uid or stat.S_IMODE(env_details.st_mode) != 0o600:
            raise ValueError(f"unsafe instance environment ownership or mode for {public_id}")
        expected_port = 9101 + offset
        if env_file.read_text(encoding="utf-8") != f"CODE_SERVER_PORT={expected_port}\n":
            raise ValueError(f"invalid instance port environment for {public_id}")
        ports.append(expected_port)
        users.append(GatewayUser(public_id, expected_port))

        credential = credential_dir / f"{public_id}.token"
        details = _regular_file(credential, "credential")
        if details.st_uid != credential_owner_uid or stat.S_IMODE(details.st_mode) not in {0o400, 0o600}:
            raise ValueError(f"unsafe credential ownership or mode for {public_id}")
        token = credential.read_text(encoding="utf-8").strip()
        if not token or not hmac.compare_digest(token_hash(token), by_id[public_id]["api_token_hash"] or ""):
            raise ValueError(f"credential hash mismatch for {public_id}")

    caddy_details = _regular_file(caddyfile, "Caddy configuration")
    if caddy_details.st_uid != config_owner_uid or stat.S_IMODE(caddy_details.st_mode) != 0o644:
        raise ValueError("unsafe Caddy configuration ownership or mode")
    if caddyfile.read_text(encoding="utf-8") != render_caddyfile(users):
        raise ValueError("Caddy routes differ from release port mapping")
    return {"instances": len(instances), "ports": ports}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--env-dir", required=True, type=Path)
    parser.add_argument("--credential-dir", required=True, type=Path)
    parser.add_argument("--caddyfile", required=True, type=Path)
    args = parser.parse_args()
    result = verify_bootstrap(
        database=Database(args.database), release=args.release,
        workspace_root=args.workspace_root, env_dir=args.env_dir,
        credential_dir=args.credential_dir, caddyfile=args.caddyfile,
    )
    print(f"Verified {result['instances']} managed instance(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
