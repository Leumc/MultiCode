import pathlib

from fastapi.testclient import TestClient

from remote_dev.app import create_app
from remote_dev.database import Database
from remote_dev.runner import LocalCppRunner, RunnerLimits
from remote_dev.worker import JobWorker


def setup(tmp_path: pathlib.Path):
    database = Database(tmp_path / "control.db")
    database.initialize()
    database.create_admin("admin", "correct horse battery staple")
    app = create_app(
        database=database,
        workspace_root=tmp_path / "ws",
        secure_cookies=False,
        workspace_size_mib=64,
    )
    client = TestClient(app)
    login = client.post("/api/admin/login", json={
        "username": "admin", "password": "correct horse battery staple"
    })
    user = client.post(
        "/api/admin/users",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={"username": "alice", "password": "developer passphrase 123"},
    ).json()
    auth = {"Authorization": f"Bearer {user['api_token']}"}
    runner = LocalCppRunner(tmp_path / "runner", RunnerLimits(
        compile_wall_ms=10_000,
        compile_memory_mib=512,
        stdout_limit_bytes=32_768,
        stderr_limit_bytes=16_384,
        total_wall_ms=10_000,
    ))
    return client, database, auth, JobWorker(database, runner)


def test_worker_claims_executes_and_persists_case_results(tmp_path):
    client, _, auth, worker = setup(tmp_path)
    submitted = client.post("/api/jobs", headers=auth, json={
        "filename": "main.cpp",
        "source": "#include <iostream>\nint main(){int x;std::cin>>x;std::cout<<x+1;}",
        "compiler": "gcc-14-gnu++17",
        "time_limit_ms": 1000,
        "memory_limit_mib": 64,
        "inputs": ["1\n", "41\n"],
    })
    job_id = submitted.json()["id"]
    assert worker.process_one() == job_id

    result = client.get(f"/api/jobs/{job_id}", headers=auth)
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "completed"
    assert body["compile_status"] == "completed"
    assert [case["stdout"] for case in body["cases"]] == ["2", "42"]
    assert "source" not in body
    assert all("input_text" not in case for case in body["cases"])


def test_queued_job_can_be_cancelled_and_worker_does_not_execute_it(tmp_path):
    client, _, auth, worker = setup(tmp_path)
    job_id = client.post("/api/jobs", headers=auth, json={
        "filename": "main.cpp", "source": "int main(){}",
        "compiler": "gcc-14-gnu++17", "time_limit_ms": 1000,
        "memory_limit_mib": 16, "inputs": [""],
    }).json()["id"]
    cancelled = client.post(f"/api/jobs/{job_id}/cancel", headers=auth, json={})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert worker.process_one() is None


def test_compile_error_is_persisted_with_original_stderr(tmp_path):
    client, _, auth, worker = setup(tmp_path)
    job_id = client.post("/api/jobs", headers=auth, json={
        "filename": "main.cpp", "source": "int main( {",
        "compiler": "gcc-14-gnu++17", "time_limit_ms": 1000,
        "memory_limit_mib": 16, "inputs": [""],
    }).json()["id"]
    worker.process_one()
    body = client.get(f"/api/jobs/{job_id}", headers=auth).json()
    assert body["status"] == "compile_error"
    assert "error:" in body["compile_stderr"]
    assert body["cases"][0]["status"] == "cancelled"
