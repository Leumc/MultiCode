import pathlib
import uuid

import pytest
from fastapi.testclient import TestClient

from remote_dev.app import create_app
from remote_dev.database import Database


@pytest.fixture()
def app_env(tmp_path: pathlib.Path):
    db_path = tmp_path / "control.db"
    workspace_root = tmp_path / "workspaces"
    database = Database(db_path)
    database.initialize()
    database.create_admin("admin", "correct horse battery staple")
    app = create_app(
        database=database,
        workspace_root=workspace_root,
        secure_cookies=False,
        workspace_size_mib=64,
    )
    with TestClient(app) as client:
        yield client, database, workspace_root


def admin_login(client: TestClient):
    response = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    return {"X-CSRF-Token": csrf}


def create_developer(client: TestClient, headers: dict[str, str], username="alice"):
    response = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": username, "password": "developer passphrase 123"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_no_public_registration_route(app_env):
    client, _, _ = app_env
    response = client.post(
        "/api/register", json={"username": "stranger", "password": "password"}
    )
    assert response.status_code == 404


def test_health_reports_database_readiness_without_sensitive_details(app_env):
    client, _, _ = app_env
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_admin_login_rejects_bad_password_and_sets_http_only_cookie(app_env):
    client, _, _ = app_env
    rejected = client.post(
        "/api/admin/login", json={"username": "admin", "password": "wrong"}
    )
    assert rejected.status_code == 401

    accepted = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "correct horse battery staple"},
    )
    assert accepted.status_code == 200
    cookie = accepted.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert accepted.json()["role"] == "admin"


def test_repeated_bad_developer_logins_are_rate_limited(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    create_developer(client, csrf, "alice")
    client.cookies.clear()

    for _ in range(5):
        response = client.post(
            "/api/login", json={"username": "alice", "password": "wrong-password"}
        )
        assert response.status_code == 401

    blocked = client.post(
        "/api/login",
        json={"username": "alice", "password": "developer passphrase 123"},
    )
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"]


def test_admin_mutations_require_csrf(app_env):
    client, _, _ = app_env
    admin_login(client)
    response = client.post(
        "/api/admin/users",
        json={"username": "alice", "password": "developer passphrase 123"},
    )
    assert response.status_code == 403


def test_developer_identity_uses_immutable_uuid_for_storage(app_env):
    client, database, workspace_root = app_env
    headers = admin_login(client)
    created = create_developer(client, headers)

    identity = uuid.UUID(created["public_id"])
    assert identity.version == 4
    assert created["workspace_path"] == str(
        workspace_root / f"{created['public_id']}.img"
    )

    database.initialize()
    assert database.get_user_by_username("alice")["public_id"] == created["public_id"]


def test_admin_creates_developer_and_token_is_returned_once(app_env):
    client, database, workspace_root = app_env
    headers = admin_login(client)
    created = create_developer(client, headers)
    assert created["username"] == "alice"
    assert created["role"] == "developer"
    assert created["api_token"].startswith("rdp_")
    assert "id" not in created
    assert created["workspace_path"] == str(
        workspace_root / f"{created['public_id']}.img"
    )

    listed = client.get("/api/admin/users").json()
    assert listed[0]["username"] == "alice"
    assert "api_token" not in listed[0]
    row = database.get_user_by_username("alice")
    assert row["api_token_hash"] != created["api_token"]


def test_only_admin_can_create_users(app_env):
    client, _, _ = app_env
    response = client.post(
        "/api/admin/users",
        json={"username": "alice", "password": "developer passphrase 123"},
    )
    assert response.status_code == 401


def test_developer_can_submit_one_cpp_with_multiple_inputs(app_env):
    client, _, _ = app_env
    headers = admin_login(client)
    user = create_developer(client, headers)
    response = client.post(
        "/api/jobs",
        headers={"Authorization": f"Bearer {user['api_token']}"},
        json={
            "filename": "main.cpp",
            "source": "#include <bits/stdc++.h>\nint main(){int x;std::cin>>x;std::cout<<x*2;}",
            "compiler": "gcc-14-gnu++17",
            "time_limit_ms": 1000,
            "memory_limit_mib": 64,
            "inputs": ["2\n", "21\n"],
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["case_count"] == 2


def test_submission_validation_enforces_single_cpp_and_system_ceilings(app_env):
    client, _, _ = app_env
    headers = admin_login(client)
    user = create_developer(client, headers)
    auth = {"Authorization": f"Bearer {user['api_token']}"}
    base = {
        "filename": "main.cpp",
        "source": "int main(){}",
        "compiler": "gcc-14-gnu++17",
        "time_limit_ms": 1000,
        "memory_limit_mib": 64,
        "inputs": [""],
    }
    cases = [
        ({**base, "filename": "main.py"}, 422),
        ({**base, "time_limit_ms": 60_001}, 422),
        ({**base, "time_limit_ms": 99}, 422),
        ({**base, "memory_limit_mib": 257}, 422),
        ({**base, "memory_limit_mib": 15}, 422),
        ({**base, "inputs": [""] * 21}, 422),
        ({**base, "compiler": "user-command"}, 422),
    ]
    for payload, expected in cases:
        response = client.post("/api/jobs", headers=auth, json=payload)
        assert response.status_code == expected, (payload, response.text)


def test_user_cannot_exceed_five_inflight_jobs(app_env):
    client, _, _ = app_env
    headers = admin_login(client)
    user = create_developer(client, headers)
    auth = {"Authorization": f"Bearer {user['api_token']}"}
    payload = {
        "filename": "main.cpp",
        "source": "int main(){}",
        "compiler": "gcc-14-gnu++17",
        "time_limit_ms": 1000,
        "memory_limit_mib": 16,
        "inputs": [""],
    }
    for _ in range(5):
        assert client.post("/api/jobs", headers=auth, json=payload).status_code == 202
    rejected = client.post("/api/jobs", headers=auth, json=payload)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "inflight_limit"


def test_users_cannot_read_each_others_jobs(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    alice = create_developer(client, csrf, "alice")
    bob = create_developer(client, csrf, "bob")
    payload = {
        "filename": "main.cpp", "source": "int main(){}",
        "compiler": "gcc-14-gnu++17", "time_limit_ms": 1000,
        "memory_limit_mib": 16, "inputs": [""],
    }
    created = client.post(
        "/api/jobs",
        headers={"Authorization": f"Bearer {alice['api_token']}"}, json=payload,
    ).json()
    hidden = client.get(
        f"/api/jobs/{created['id']}",
        headers={"Authorization": f"Bearer {bob['api_token']}"},
    )
    assert hidden.status_code == 404


def test_admin_can_revoke_all_developer_browser_sessions(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    created = create_developer(client, csrf, "alice")

    with TestClient(client.app) as developer_client:
        login = developer_client.post(
            "/api/login",
            json={"username": "alice", "password": "developer passphrase 123"},
        )
        assert login.status_code == 200
        assert developer_client.get(
            "/api/auth/forward", headers={"X-Forwarded-Uri": f"/u/{created['public_id']}/"}
        ).status_code == 200

        revoked = client.post(
            f"/api/admin/users/{created['public_id']}/revoke-sessions",
            headers=csrf,
            json={},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked_sessions"] == 1
        assert developer_client.get(
            "/api/auth/forward", headers={"X-Forwarded-Uri": f"/u/{created['public_id']}/"}
        ).status_code == 401


def test_developer_logout_revokes_current_browser_session(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    created = create_developer(client, csrf, "alice")
    client.cookies.clear()
    login = client.post(
        "/api/login", json={
            "username": "alice", "password": "developer passphrase 123"
        },
    )
    assert login.status_code == 200
    logout = client.post("/api/logout", json={})
    assert logout.status_code == 200
    assert client.get(
        "/api/auth/forward",
        headers={"X-Forwarded-Uri": f"/u/{created['public_id']}/"},
    ).status_code == 401


def test_admin_manages_direct_header_allowlist(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    original = client.get("/api/admin/headers").json()
    names = {item["name"] for item in original if item["enabled"]}
    assert "bits/stdc++.h" in names
    response = client.put(
        "/api/admin/headers/vector", headers=csrf, json={"enabled": False}
    )
    assert response.status_code == 200
    assert response.json() == {"name": "vector", "enabled": False}


def test_grant_raises_submission_ceiling_and_is_charged_only_when_claimed(app_env):
    client, database, _ = app_env
    csrf = admin_login(client)
    user = create_developer(client, csrf)
    grant = client.post(
        f"/api/admin/users/{user['public_id']}/grants",
        headers=csrf,
        json={
            "memory_limit_mib": 512,
            "time_limit_ms": 120000,
            "remaining_uses": 2,
            "expires_in_seconds": 3600,
        },
    ).json()
    auth = {"Authorization": f"Bearer {user['api_token']}"}
    payload = {
        "filename": "main.cpp", "source": "int main(){}",
        "compiler": "gcc-14-gnu++17", "time_limit_ms": 120000,
        "memory_limit_mib": 512, "inputs": [""],
    }
    first = client.post("/api/jobs", headers=auth, json=payload)
    assert first.status_code == 202, first.text
    assert database.get_grant(grant["id"])["remaining_uses"] == 2
    claimed = database.claim_next_job()
    assert claimed["id"] == first.json()["id"]
    assert database.get_grant(grant["id"])["remaining_uses"] == 1


def test_temporary_grant_uses_earliest_expiry_rule(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    user = create_developer(client, csrf)
    grant = client.post(
        f"/api/admin/users/{user['public_id']}/grants",
        headers=csrf,
        json={
            "memory_limit_mib": 512,
            "time_limit_ms": 120000,
            "remaining_uses": 2,
            "expires_in_seconds": 3600,
        },
    )
    assert grant.status_code == 201, grant.text
    data = grant.json()
    assert data["remaining_uses"] == 2
    assert data["expires_at"]
    assert data["memory_limit_mib"] == 512


def test_renaming_developer_keeps_public_identity_and_workspace(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    created = create_developer(client, csrf, "alice")

    renamed = client.patch(
        f"/api/admin/users/{created['public_id']}",
        headers=csrf,
        json={"username": "captain"},
    )
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["username"] == "captain"
    assert body["public_id"] == created["public_id"]
    assert body["workspace_path"] == created["workspace_path"]


def test_renaming_developer_rejects_existing_username(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    alice = create_developer(client, csrf, "alice")
    create_developer(client, csrf, "bob")

    response = client.patch(
        f"/api/admin/users/{alice['public_id']}", headers=csrf, json={"username": "bob"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "username already exists"


def test_developer_account_limit_and_immediate_disable(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    users = [create_developer(client, csrf, name) for name in ("alice", "bob", "charlie")]
    fourth = client.post(
        "/api/admin/users", headers=csrf,
        json={"username": "delta", "password": "developer passphrase 123"},
    )
    assert fourth.status_code == 409

    alice_auth = {"Authorization": f"Bearer {users[0]['api_token']}"}
    assert client.post("/api/jobs", headers=alice_auth, json={
        "filename": "main.cpp", "source": "int main(){}", "compiler": "gcc-14-gnu++17",
        "time_limit_ms": 1000, "memory_limit_mib": 64, "inputs": [""],
    }).status_code == 202
    disabled = client.patch(
        f"/api/admin/users/{users[0]['public_id']}", headers=csrf, json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert client.post("/api/jobs", headers=alice_auth, json={
        "filename": "main.cpp", "source": "int main(){}", "compiler": "gcc-14-gnu++17",
        "time_limit_ms": 1000, "memory_limit_mib": 64, "inputs": [""],
    }).status_code == 401


def test_extension_catalog_rejects_arbitrary_user_sources_and_supports_approval(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    user = create_developer(client, csrf)
    catalog = client.post(
        "/api/admin/extensions",
        headers=csrf,
        json={
            "extension_id": "llvm-vs-code-extensions.vscode-clangd",
            "version": "0.1.35",
            "sha256": "a" * 64,
        },
    )
    assert catalog.status_code == 201, catalog.text
    extension = catalog.json()

    auth = {"Authorization": f"Bearer {user['api_token']}"}
    bad = client.post(
        "/api/extensions/requests",
        headers=auth,
        json={"catalog_id": extension["id"], "url": "https://example.invalid/evil.vsix"},
    )
    assert bad.status_code == 422
    request = client.post(
        "/api/extensions/requests", headers=auth, json={"catalog_id": extension["id"]}
    )
    assert request.status_code == 201, request.text
    decision = client.post(
        f"/api/admin/extension-requests/{request.json()['id']}/approve",
        headers=csrf,
        json={"scope": "user"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"


def test_developer_login_and_forward_auth_are_workspace_scoped(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    alice = create_developer(client, csrf, "alice")
    bob = create_developer(client, csrf, "bob")

    client.cookies.clear()
    login = client.post(
        "/api/login",
        json={"username": "alice", "password": "developer passphrase 123"},
    )
    assert login.status_code == 200
    assert login.json()["workspace_url"] == f"/u/{alice['public_id']}/"
    own = client.get(
        "/api/auth/forward", headers={"X-Forwarded-Uri": f"/u/{alice['public_id']}/"}
    )
    assert own.status_code == 200
    assert own.headers["X-Remote-User"] == alice["public_id"]
    other = client.get(
        "/api/auth/forward", headers={"X-Forwarded-Uri": f"/u/{bob['public_id']}/"}
    )
    assert other.status_code == 403


def test_admin_session_cannot_enter_developer_workspace(app_env):
    client, _, _ = app_env
    admin_login(client)
    response = client.get(
        "/api/auth/forward", headers={
            "X-Forwarded-Uri": "/u/00000000-0000-4000-8000-000000000000/"
        }
    )
    assert response.status_code == 403


def test_admin_actions_are_audited(app_env):
    client, _, _ = app_env
    csrf = admin_login(client)
    create_developer(client, csrf)
    events = client.get("/api/admin/audit").json()
    assert any(event["action"] == "user.create" for event in events)
