import threading

from remote_dev.database import Database
from remote_dev.runner import CompileResult, ExecutionResult


def database_with_users(tmp_path):
    database = Database(tmp_path / "control.db")
    database.initialize()
    alice = database.create_developer("alice", "developer passphrase 123", "alice-token", "/tmp/alice")
    bob = database.create_developer("bob", "developer passphrase 456", "bob-token", "/tmp/bob")
    return database, alice, bob


def submit(database, user_id, *, memory_mib=64):
    return database.create_job(user_id, {
        "filename": "main.cpp",
        "source": "int main(){}",
        "compiler": "gcc-14-gnu++17",
        "time_limit_ms": 1_000,
        "memory_limit_mib": memory_mib,
        "inputs": [""],
    })["id"]


def finish(database, job_id):
    database.finish_job(
        job_id,
        ExecutionResult("completed", CompileResult("completed", exit_code=0), []),
    )


def test_claim_round_robins_across_users_even_after_previous_job_finishes(tmp_path):
    database, alice, bob = database_with_users(tmp_path)
    alice_first = submit(database, alice["id"])
    submit(database, alice["id"])
    bob_first = submit(database, bob["id"])

    assert database.claim_next_job()["id"] == alice_first
    finish(database, alice_first)

    assert database.claim_next_job()["id"] == bob_first


def test_claim_reserves_aggregate_active_memory_until_job_finishes(tmp_path):
    database, alice, bob = database_with_users(tmp_path)
    alice_first = submit(database, alice["id"], memory_mib=160)
    assert database.claim_next_job()["id"] == alice_first

    alice_second = submit(database, alice["id"], memory_mib=160)
    bob_job = submit(database, bob["id"], memory_mib=64)
    assert database.claim_next_job()["id"] == bob_job

    finish(database, alice_first)
    assert database.claim_next_job()["id"] == alice_second


def test_system_error_releases_the_active_memory_reservation(tmp_path):
    database, alice, _ = database_with_users(tmp_path)
    first = submit(database, alice["id"], memory_mib=160)
    second = submit(database, alice["id"], memory_mib=160)

    assert database.claim_next_job()["id"] == first
    assert database.claim_next_job() is None
    database.mark_job_system_error(first, "runner failed")

    assert database.claim_next_job()["id"] == second


def test_concurrent_claims_cannot_overbook_one_users_memory(tmp_path):
    database, alice, _ = database_with_users(tmp_path)
    job_ids = {submit(database, alice["id"], memory_mib=160) for _ in range(2)}
    barrier = threading.Barrier(3)
    claimed = []

    def claim():
        barrier.wait()
        result = database.claim_next_job()
        claimed.append(None if result is None else result["id"])

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len([job_id for job_id in claimed if job_id is not None]) == 1
    assert set(claimed) - {None} <= job_ids
