"""Job worker isolated from control-plane persistence by a narrow broker."""

from __future__ import annotations

import threading

from .broker import JobBroker
from .constants import DEFAULT_GLOBAL_RUN_SLOTS
from .runner import CaseSpec, CompileSpec, LocalCppRunner


class JobWorker:
    def __init__(self, broker: JobBroker, runner: LocalCppRunner):
        self.broker = broker
        self.runner = runner

    def process_one(self) -> int | None:
        job = self.broker.claim_next_job()
        if not job:
            return None
        job_id = job["id"]
        try:
            execution = self.runner.execute(
                CompileSpec(
                    job["filename"], job["source"], job["compiler"],
                    job_id=job_id, owner_public_id=job["owner_public_id"],
                ),
                [
                    CaseSpec(
                        case["input_text"], job["time_limit_ms"], job["memory_limit_mib"]
                    )
                    for case in job["cases"]
                ],
                cancel_check=lambda: self.broker.is_cancel_requested(job_id),
            )
            self.broker.finish_job(job_id, execution)
        except Exception as error:
            self.broker.mark_job_system_error(job_id, f"{type(error).__name__}: {error}")
        return job_id

    def run_forever(
        self,
        poll_interval: float,
        *,
        slots: int = DEFAULT_GLOBAL_RUN_SLOTS,
        stop_event: threading.Event | None = None,
    ) -> None:
        if slots < 1:
            raise ValueError("slots must be positive")
        stop = stop_event or threading.Event()

        def run_slot() -> None:
            while not stop.is_set():
                if self.process_one() is None:
                    stop.wait(poll_interval)

        threads = [
            threading.Thread(target=run_slot, name=f"job-worker-{index + 1}")
            for index in range(slots)
        ]
        for thread in threads:
            thread.start()
        try:
            while any(thread.is_alive() for thread in threads):
                for thread in threads:
                    thread.join(timeout=0.1)
        finally:
            stop.set()
            for thread in threads:
                thread.join()
