import threading

from remote_dev.runner import CompileResult, ExecutionResult
from remote_dev.worker import JobWorker


def job(job_id):
    return {
        "id": job_id,
        "owner_public_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "filename": "main.cpp",
        "source": "int main(){}",
        "compiler": "gcc-14-gnu++17",
        "time_limit_ms": 1_000,
        "memory_limit_mib": 64,
        "cases": [],
    }


def test_worker_loop_processes_two_jobs_concurrently_by_default():
    claim_lock = threading.Lock()
    queued = [job(1), job(2), job(3)]
    both_running = threading.Event()
    release = threading.Event()
    all_finished = threading.Event()
    stop = threading.Event()
    active = 0
    maximum_active = 0
    finished = []

    class Broker:
        def claim_next_job(self):
            with claim_lock:
                return queued.pop(0) if queued else None

        def is_cancel_requested(self, job_id):
            return False

        def finish_job(self, job_id, execution):
            finished.append(job_id)
            if len(finished) == 3:
                all_finished.set()

        def mark_job_system_error(self, job_id, message):
            raise AssertionError(message)

    class Runner:
        def execute(self, compile_spec, cases, cancel_check=None):
            nonlocal active, maximum_active
            with claim_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 2:
                    both_running.set()
            release.wait(timeout=5)
            with claim_lock:
                active -= 1
            return ExecutionResult("completed", CompileResult("completed"), [])

    worker = JobWorker(Broker(), Runner())
    loop = threading.Thread(
        target=worker.run_forever,
        kwargs={"poll_interval": 0.001, "stop_event": stop},
    )
    loop.start()
    try:
        assert both_running.wait(timeout=2)
        assert maximum_active == 2
        with claim_lock:
            assert len(queued) == 1
        release.set()
        assert all_finished.wait(timeout=2)
    finally:
        stop.set()
        release.set()
        loop.join(timeout=5)

    assert not loop.is_alive()
    assert sorted(finished) == [1, 2, 3]
