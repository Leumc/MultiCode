import os
from pathlib import Path

import pytest

from remote_dev.database import Database
from remote_dev.gateway_config import GatewayUser, render_caddyfile
from remote_dev.verify_bootstrap import verify_bootstrap


UUIDS = [
    "550e8400-e29b-41d4-a716-446655440000",
    "123e4567-e89b-42d3-a456-426614174000",
]


def complete_layout(tmp_path: Path):
    release = tmp_path / "release"
    workspaces = tmp_path / "workspaces"
    envs = tmp_path / "code-server"
    credentials = tmp_path / "credentials"
    for path in (release, workspaces, envs, credentials):
        path.mkdir()
    (release / "instances").write_text("\n".join(UUIDS) + "\n")
    (envs / "locked-settings.json").write_text("{}\n")
    (envs / "locked-keybindings.json").write_text("[]\n")
    (envs / "locked-settings.json").chmod(0o644)
    (envs / "locked-keybindings.json").chmod(0o644)
    database = Database(tmp_path / "control.db")
    database.initialize()
    users = []
    for offset, public_id in enumerate(UUIDS):
        token = f"rdp_secret_{offset}"
        workspace = workspaces / f"{public_id}.img"
        workspace.write_bytes(b"image")
        (envs / f"{public_id}.env").write_text(f"CODE_SERVER_PORT={9101 + offset}\n")
        (envs / f"{public_id}.env").chmod(0o600)
        credential = credentials / f"{public_id}.token"
        credential.write_text(token)
        credential.chmod(0o600)
        users.append(database.create_developer(
            f"user{offset}", "developer passphrase 123", token, str(workspace),
            public_id=public_id,
        ))
    caddy = tmp_path / "Caddyfile"
    caddy.write_text(render_caddyfile([
        GatewayUser(public_id, 9101 + offset)
        for offset, public_id in enumerate(UUIDS)
    ]))
    caddy.chmod(0o644)
    return database, release, workspaces, envs, credentials, caddy


def run_verify(layout):
    database, release, workspaces, envs, credentials, caddy = layout
    connection = database.connect()
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        connection.close()
    return verify_bootstrap(
        database=database, release=release, workspace_root=workspaces,
        env_dir=envs, credential_dir=credentials, caddyfile=caddy,
        credential_owner_uid=os.getuid(),
        config_owner_uid=os.getuid(),
    )


def test_verify_bootstrap_checks_complete_roster_and_hashes(tmp_path, capsys):
    result = run_verify(complete_layout(tmp_path))
    assert result == {"instances": 2, "ports": [9101, 9102]}
    assert "rdp_secret" not in capsys.readouterr().out


@pytest.mark.parametrize("corruption", ["db", "workspace", "env", "port", "caddy", "credential", "hash"])
def test_verify_bootstrap_rejects_every_mismatch(tmp_path, corruption):
    layout = complete_layout(tmp_path)
    database, _, workspaces, envs, credentials, caddy = layout
    first = UUIDS[0]
    if corruption == "db":
        with database.connect() as connection:
            connection.execute("DELETE FROM users WHERE public_id=?", (first,))
    elif corruption == "workspace":
        (workspaces / f"{first}.img").unlink()
    elif corruption == "env":
        (envs / f"{first}.env").unlink()
    elif corruption == "port":
        (envs / f"{first}.env").write_text("CODE_SERVER_PORT=9103\n")
    elif corruption == "caddy":
        caddy.write_text(caddy.read_text().replace("127.0.0.1:9101", "127.0.0.1:9103"))
    elif corruption == "credential":
        (credentials / f"{first}.token").unlink()
    elif corruption == "hash":
        (credentials / f"{first}.token").write_text("rdp_wrong")
    with pytest.raises(ValueError):
        run_verify(layout)


@pytest.mark.parametrize("target", ["env", "locked", "caddy"])
def test_verify_bootstrap_rejects_unsafe_config_modes(tmp_path, target):
    layout = complete_layout(tmp_path)
    _, _, _, envs, _, caddy = layout
    if target == "env":
        (envs / f"{UUIDS[0]}.env").chmod(0o666)
    elif target == "locked":
        (envs / "locked-settings.json").chmod(0o666)
    else:
        caddy.chmod(0o666)
    with pytest.raises(ValueError):
        run_verify(layout)
