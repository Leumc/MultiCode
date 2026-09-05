import argparse
import sqlite3
from pathlib import Path

import pytest

from remote_dev import cli
from remote_dev.database import Database
from remote_dev.security import token_hash


UUID = "550e8400-e29b-41d4-a716-446655440000"


def layout(tmp_path: Path):
    release = tmp_path / "release"
    release.mkdir()
    (release / "instances").write_text(f"{UUID}\n", encoding="utf-8")
    data = tmp_path / "data"
    workspaces = tmp_path / "workspaces"
    credentials = tmp_path / "credentials"
    workspaces.mkdir()
    credentials.mkdir(mode=0o700)
    (workspaces / f"{UUID}.img").write_bytes(b"workspace")
    return release, data, workspaces, credentials


def test_bootstrap_instance_uses_release_uuid_and_never_emits_token(tmp_path, capsys):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()

    user = cli.bootstrap_instance(
        database=database,
        release=release,
        workspace_root=workspaces,
        credential_dir=credentials,
        public_id=UUID,
        username="alice",
        password="developer passphrase 123",
        token_factory=lambda: "rdp_top_secret",
    )

    captured = capsys.readouterr()
    assert "rdp_top_secret" not in captured.out + captured.err
    assert user["public_id"] == UUID
    assert user["workspace_path"] == str(workspaces / f"{UUID}.img")
    credential = credentials / f"{UUID}.token"
    assert credential.read_text(encoding="utf-8") == "rdp_top_secret"
    assert credential.stat().st_mode & 0o777 == 0o600
    row = database.get_user_by_public_id(UUID)
    assert row["api_token_hash"] == token_hash("rdp_top_secret")
    assert "rdp_top_secret" not in row.values()


def test_bootstrap_instance_retries_complete_state_without_overwriting(tmp_path):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()
    kwargs = dict(
        database=database, release=release, workspace_root=workspaces,
        credential_dir=credentials, public_id=UUID, username="alice",
        password="developer passphrase 123",
    )
    cli.bootstrap_instance(**kwargs, token_factory=lambda: "rdp_first")
    user = cli.bootstrap_instance(**kwargs, token_factory=lambda: "rdp_second")
    assert user["public_id"] == UUID
    assert (credentials / f"{UUID}.token").read_text() == "rdp_first"
    assert database.get_user_by_public_id(UUID)["api_token_hash"] == token_hash("rdp_first")


def test_bootstrap_instance_removes_staged_secret_when_database_insert_fails(tmp_path, monkeypatch):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected")

    monkeypatch.setattr(database, "create_developer", fail)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        cli.bootstrap_instance(
            database=database, release=release, workspace_root=workspaces,
            credential_dir=credentials, public_id=UUID, username="alice",
            password="developer passphrase 123", token_factory=lambda: "rdp_secret",
        )
    assert list(credentials.iterdir()) == []


def test_bootstrap_instance_recovers_db_commit_with_matching_staged_credential(tmp_path):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()
    token = "rdp_recoverable_secret"
    workspace = workspaces / f"{UUID}.img"
    database.create_developer(
        "alice", "developer passphrase 123", token, str(workspace), public_id=UUID,
    )
    staged = credentials / f".{UUID}.token.99999"
    staged.write_text(token, encoding="utf-8")
    staged.chmod(0o600)

    user = cli.bootstrap_instance(
        database=database, release=release, workspace_root=workspaces,
        credential_dir=credentials, public_id=UUID, username="alice",
        password="unused retry password", token_factory=lambda: "must_not_replace",
    )

    assert user["public_id"] == UUID
    assert (credentials / f"{UUID}.token").read_text(encoding="utf-8") == token
    assert not staged.exists()


def test_bootstrap_instance_cleans_matching_staging_after_final_link_crash(tmp_path):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()
    kwargs = dict(
        database=database, release=release, workspace_root=workspaces,
        credential_dir=credentials, public_id=UUID, username="alice",
        password="developer passphrase 123",
    )
    cli.bootstrap_instance(**kwargs, token_factory=lambda: "rdp_first")
    final = credentials / f"{UUID}.token"
    staged = credentials / f".{UUID}.token.99999"
    cli.os.link(final, staged)

    cli.bootstrap_instance(**kwargs, token_factory=lambda: "rdp_second")

    assert final.read_text(encoding="utf-8") == "rdp_first"
    assert not staged.exists()


def test_bootstrap_instance_fsyncs_staging_directory_before_database_commit(tmp_path, monkeypatch):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()
    events = []
    real_fsync = cli.os.fsync
    real_create = database.create_developer

    def record_fsync(fd):
        if __import__("stat").S_ISDIR(cli.os.fstat(fd).st_mode):
            events.append("directory-fsync")
        return real_fsync(fd)

    def record_create(*args, **kwargs):
        events.append("database-commit")
        return real_create(*args, **kwargs)

    monkeypatch.setattr(cli.os, "fsync", record_fsync)
    monkeypatch.setattr(database, "create_developer", record_create)
    cli.bootstrap_instance(
        database=database, release=release, workspace_root=workspaces,
        credential_dir=credentials, public_id=UUID, username="alice",
        password="developer passphrase 123", token_factory=lambda: "rdp_secret",
    )
    assert events.index("directory-fsync") < events.index("database-commit")


def test_bootstrap_instance_preserves_recoverable_staging_when_compensation_fails(tmp_path, monkeypatch):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()
    real_link = cli.os.link
    real_delete = database.delete_unconfigured_developer

    monkeypatch.setattr(cli.os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("publish failed")))
    monkeypatch.setattr(
        database, "delete_unconfigured_developer",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("compensation failed")),
    )
    with pytest.raises(RuntimeError, match="recoverable staging was preserved"):
        cli.bootstrap_instance(
            database=database, release=release, workspace_root=workspaces,
            credential_dir=credentials, public_id=UUID, username="alice",
            password="developer passphrase 123", token_factory=lambda: "rdp_recoverable",
        )
    staged = list(credentials.glob(f".{UUID}.token.*"))
    assert len(staged) == 1
    assert database.get_user_by_public_id(UUID) is not None

    monkeypatch.setattr(cli.os, "link", real_link)
    monkeypatch.setattr(database, "delete_unconfigured_developer", real_delete)
    user = cli.bootstrap_instance(
        database=database, release=release, workspace_root=workspaces,
        credential_dir=credentials, public_id=UUID, username="alice",
        password="unused retry password", token_factory=lambda: "must_not_replace",
    )
    assert user["public_id"] == UUID
    assert not staged[0].exists()


def test_bootstrap_instance_fsyncs_directory_on_complete_state_retry(tmp_path, monkeypatch):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()
    kwargs = dict(
        database=database, release=release, workspace_root=workspaces,
        credential_dir=credentials, public_id=UUID, username="alice",
        password="developer passphrase 123",
    )
    cli.bootstrap_instance(**kwargs, token_factory=lambda: "rdp_first")
    directory_fsyncs = []
    real_fsync = cli.os.fsync

    def record_fsync(fd):
        if __import__("stat").S_ISDIR(cli.os.fstat(fd).st_mode):
            directory_fsyncs.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(cli.os, "fsync", record_fsync)
    cli.bootstrap_instance(**kwargs, token_factory=lambda: "must_not_replace")
    assert len(directory_fsyncs) == 1


def test_bootstrap_instance_never_overwrites_concurrently_created_credential(tmp_path, monkeypatch):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()
    destination = credentials / f"{UUID}.token"
    original_link = cli.os.link

    def race_link(source, target, **kwargs):
        Path(target).write_text("concurrent-owner-token", encoding="utf-8")
        return original_link(source, target, **kwargs)

    monkeypatch.setattr(cli.os, "link", race_link)
    with pytest.raises(FileExistsError):
        cli.bootstrap_instance(
            database=database, release=release, workspace_root=workspaces,
            credential_dir=credentials, public_id=UUID, username="alice",
            password="developer passphrase 123", token_factory=lambda: "rdp_secret",
        )
    assert destination.read_text(encoding="utf-8") == "concurrent-owner-token"
    assert database.get_user_by_public_id(UUID) is None
    assert [path.name for path in credentials.iterdir()] == [f"{UUID}.token"]


def test_bootstrap_instance_compensates_database_when_credential_publish_fails(tmp_path, monkeypatch):
    release, data, workspaces, credentials = layout(tmp_path)
    database = Database(data / "control.db")
    database.initialize()

    def fail_link(*args, **kwargs):
        raise OSError("injected publish failure")

    monkeypatch.setattr(cli.os, "link", fail_link)
    with pytest.raises(OSError, match="injected publish failure"):
        cli.bootstrap_instance(
            database=database, release=release, workspace_root=workspaces,
            credential_dir=credentials, public_id=UUID, username="alice",
            password="developer passphrase 123", token_factory=lambda: "rdp_secret",
        )
    assert database.get_user_by_public_id(UUID) is None
    assert list(credentials.iterdir()) == []


def test_init_admin_uses_private_umask_and_restores_caller_umask(monkeypatch, tmp_path):
    calls = []

    class FakeDatabase:
        def __init__(self, path):
            self.path = path

        def initialize(self):
            return None

        def get_user_by_username(self, username):
            return None

        def create_admin(self, username, password):
            return {"username": username}

    def fake_umask(value):
        calls.append(value)
        return 0o022

    monkeypatch.setattr(cli, "Database", FakeDatabase)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "administrator passphrase 123")
    monkeypatch.setattr(cli.os, "umask", fake_umask)
    args = argparse.Namespace(data_dir=str(tmp_path), workspace_root=None, username="admin")
    assert cli.cmd_init_admin(args) == 0
    assert calls == [0o077, 0o022]


def test_cli_passwords_are_hidden_input_only():
    root = cli.parser()
    init = root.parse_args(["init-admin"])
    bootstrap = root.parse_args([
        "bootstrap-instance", "--instance", UUID, "--username", "alice"
    ])
    assert not hasattr(init, "password")
    assert not hasattr(bootstrap, "password")
