"""Development C++ runner with the same structured contract as the production sandbox.

This local runner is for tests and loopback development only. Production adds
bubblewrap, cgroup-v2 parent/user limits, private networking and seccomp.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable

from .constants import DEFAULT_GLOBAL_COMPILE_SLOTS, MAX_USER_RUNNING_MEMORY_MIB
from .cgroup_v2 import CgroupManager, ProcessCgroup


@dataclass(frozen=True)
class RunnerLimits:
    compile_wall_ms: int
    compile_memory_mib: int
    stdout_limit_bytes: int
    stderr_limit_bytes: int
    total_wall_ms: int


@dataclass(frozen=True)
class CompileSpec:
    filename: str
    source: str
    compiler: str
    job_id: int | None = None
    owner_public_id: str | None = None


@dataclass(frozen=True)
class CaseSpec:
    input_text: str
    time_limit_ms: int
    memory_limit_mib: int


@dataclass
class CompileResult:
    status: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    wall_time_ms: int = 0
    output_truncated: bool = False


@dataclass
class CaseResult:
    position: int
    status: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    term_signal: int | None = None
    wall_time_ms: int = 0
    peak_memory_kib: int | None = None
    output_truncated: bool = False


@dataclass
class ExecutionResult:
    status: str
    compile: CompileResult
    cases: list[CaseResult] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _limit_process(memory_mib: int, file_limit: int) -> None:
    os.setsid()
    memory = memory_mib * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def _read_limited(path: Path, limit: int) -> tuple[str, bool]:
    size = path.stat().st_size if path.exists() else 0
    with path.open("rb") as handle:
        data = handle.read(limit)
    return data.decode("utf-8", "replace"), size > limit


def _peak_rss_kib(pid: int) -> int | None:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    for key in ("VmHWM:", "VmRSS:"):
        for line in text.splitlines():
            if line.startswith(key):
                return int(line.split()[1])
    return None


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class LocalCppRunner:
    COMPILERS = {
        "gcc-14-gnu++17": ["/usr/bin/g++", "-std=gnu++17", "-O2", "-pipe", "-DONLINE_JUDGE"],
    }

    def __init__(self, work_root: str | Path, limits: RunnerLimits):
        self.work_root = Path(work_root)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.limits = limits

    def execute(
        self,
        compile_spec: CompileSpec,
        cases: list[CaseSpec],
        cancel_check: Callable[[], bool] | None = None,
    ) -> ExecutionResult:
        cancel_check = cancel_check or (lambda: False)
        if compile_spec.compiler not in self.COMPILERS:
            return ExecutionResult(
                "system_error",
                CompileResult("system_error", stderr="compiler ID is not approved"),
            )
        job_dir = Path(tempfile.mkdtemp(prefix="rdp-job-", dir=self.work_root))
        try:
            source = job_dir / self._source_filename(compile_spec)
            binary = job_dir / "main"
            source.write_text(compile_spec.source, encoding="utf-8")
            compile_result = self._compile_with_capacity(
                source, binary, compile_spec.compiler, job_dir
            )
            if compile_result.status != "completed":
                return ExecutionResult(compile_result.status, compile_result, [])
            deadline = time.monotonic() + self.limits.total_wall_ms / 1000
            results: list[CaseResult] = []
            for index, case in enumerate(cases, 1):
                if cancel_check():
                    results.extend(
                        CaseResult(position=position, status="cancelled", stderr="cancelled by user or administrator")
                        for position in range(index, len(cases) + 1)
                    )
                    return ExecutionResult("cancelled", compile_result, results)
                remaining_ms = int(max(0, deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    results.extend(
                        CaseResult(position=position, status="cancelled", stderr="submission wall limit reached")
                        for position in range(index, len(cases) + 1)
                    )
                    break
                effective = CaseSpec(
                    case.input_text,
                    min(case.time_limit_ms, remaining_ms),
                    case.memory_limit_mib,
                )
                result = self._run_case(binary, effective, index, job_dir, cancel_check)
                results.append(result)
                if result.status == "cancelled":
                    results.extend(
                        CaseResult(position=position, status="cancelled", stderr="cancelled by user or administrator")
                        for position in range(index + 1, len(cases) + 1)
                    )
                    return ExecutionResult("cancelled", compile_result, results)
            return ExecutionResult("completed", compile_result, results)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def _source_filename(self, compile_spec: CompileSpec) -> str:
        return compile_spec.filename

    def _compile_with_capacity(
        self, source: Path, binary: Path, compiler: str, job_dir: Path
    ) -> CompileResult:
        return self._compile(source, binary, compiler, job_dir)

    def _compile(
        self, source: Path, binary: Path, compiler: str, job_dir: Path
    ) -> CompileResult:
        stdout_path = job_dir / "compile.stdout"
        stderr_path = job_dir / "compile.stderr"
        command = [*self.COMPILERS[compiler], str(source), "-o", str(binary)]
        started = time.monotonic()
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=job_dir,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                preexec_fn=lambda: _limit_process(
                    self.limits.compile_memory_mib,
                    max(self.limits.stdout_limit_bytes, self.limits.stderr_limit_bytes) + 65_536,
                ),
            )
            try:
                return_code = process.wait(timeout=self.limits.compile_wall_ms / 1000)
                timed_out = False
            except subprocess.TimeoutExpired:
                _kill_group(process)
                return_code = process.wait()
                timed_out = True
        elapsed = int((time.monotonic() - started) * 1000)
        stdout_text, stdout_cut = _read_limited(stdout_path, self.limits.stdout_limit_bytes)
        stderr_text, stderr_cut = _read_limited(stderr_path, self.limits.stderr_limit_bytes)
        if timed_out:
            status = "system_error"
            stderr_text = (stderr_text + "\ncompiler wall limit exceeded").strip()
        elif return_code != 0:
            status = "compile_error"
        else:
            status = "completed"
        return CompileResult(
            status=status,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=return_code,
            wall_time_ms=elapsed,
            output_truncated=stdout_cut or stderr_cut,
        )

    def _run_case(
        self,
        binary: Path,
        case: CaseSpec,
        position: int,
        job_dir: Path,
        cancel_check: Callable[[], bool],
    ) -> CaseResult:
        prefix = f"case-{position}"
        input_path = job_dir / f"{prefix}.stdin"
        stdout_path = job_dir / f"{prefix}.stdout"
        stderr_path = job_dir / f"{prefix}.stderr"
        input_path.write_text(case.input_text, encoding="utf-8")
        started = time.monotonic()
        peak = 0
        status = "running"
        with (
            input_path.open("rb") as stdin,
            stdout_path.open("wb") as stdout,
            stderr_path.open("wb") as stderr,
        ):
            process = subprocess.Popen(
                [str(binary)],
                cwd=job_dir,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                preexec_fn=lambda: _limit_process(
                    case.memory_limit_mib,
                    max(self.limits.stdout_limit_bytes, self.limits.stderr_limit_bytes) + 65_536,
                ),
            )
            while process.poll() is None:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                observed = _peak_rss_kib(process.pid)
                if observed is not None:
                    peak = max(peak, observed)
                if cancel_check():
                    status = "cancelled"
                    _kill_group(process)
                    break
                if stdout_path.stat().st_size > self.limits.stdout_limit_bytes:
                    status = "output_limit"
                    _kill_group(process)
                    break
                if stderr_path.stat().st_size > self.limits.stderr_limit_bytes:
                    status = "output_limit"
                    _kill_group(process)
                    break
                if observed is not None and observed > case.memory_limit_mib * 1024:
                    status = "memory_limit"
                    _kill_group(process)
                    break
                if elapsed_ms >= case.time_limit_ms:
                    status = "time_limit"
                    _kill_group(process)
                    break
                time.sleep(0.005)
            return_code = process.wait()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        observed = _peak_rss_kib(process.pid)
        if observed is not None:
            peak = max(peak, observed)
        stdout_text, stdout_cut = _read_limited(stdout_path, self.limits.stdout_limit_bytes)
        stderr_text, stderr_cut = _read_limited(stderr_path, self.limits.stderr_limit_bytes)
        if status == "running":
            if stdout_cut or stderr_cut or return_code == -signal.SIGXFSZ:
                status = "output_limit"
            elif return_code == 0:
                status = "completed"
            elif "bad_alloc" in stderr_text or "cannot allocate memory" in stderr_text.lower():
                status = "memory_limit"
            else:
                status = "runtime_error"
        term_signal = -return_code if return_code < 0 else None
        return CaseResult(
            position=position,
            status=status,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=return_code if return_code >= 0 else None,
            term_signal=term_signal,
            wall_time_ms=elapsed_ms,
            peak_memory_kib=peak,
            output_truncated=(stdout_cut or stderr_cut or status == "output_limit"),
        )


class SandboxedCppRunner(LocalCppRunner):
    """Production runner slice using bubblewrap and a seccomp launcher.

    Production CLI selects this runner and requires a delegated cgroup v2
    subtree plus a separately built ``sandbox-exec`` binary from
    ``sandbox/sandbox_exec.c``.  Development may opt into LocalCppRunner only
    through the explicit unsafe flag.
    """

    COMPILERS = {
        "gcc-14-gnu++17": [
            "/usr/bin/g++",
            "-std=gnu++17",
            "-O2",
            "-pipe",
            "-DONLINE_JUDGE",
        ],
    }

    def __init__(
        self,
        work_root: str | Path,
        limits: RunnerLimits,
        *,
        sandbox_exec: str | Path = "/usr/libexec/remote-dev/sandbox-exec",
        bwrap: str | Path = "/usr/bin/bwrap",
        cgroup_manager: CgroupManager | None = None,
    ):
        super().__init__(work_root, limits)
        self._compile_semaphore = threading.Semaphore(DEFAULT_GLOBAL_COMPILE_SLOTS)
        self._context = threading.local()
        self.cgroup_manager = cgroup_manager
        self.sandbox_exec = Path(sandbox_exec).resolve()
        self.bwrap = Path(bwrap).resolve()
        for dependency in (self.bwrap, self.sandbox_exec):
            if not dependency.is_file() or not os.access(dependency, os.X_OK):
                raise FileNotFoundError(f"sandbox dependency is not executable: {dependency}")

    def execute(
        self,
        compile_spec: CompileSpec,
        cases: list[CaseSpec],
        cancel_check: Callable[[], bool] | None = None,
    ) -> ExecutionResult:
        if self.cgroup_manager is not None:
            if compile_spec.job_id is None or compile_spec.owner_public_id is None:
                raise ValueError("production cgroup execution requires job and owner identity")
            self._context.job_id = compile_spec.job_id
            self._context.owner_public_id = compile_spec.owner_public_id
        try:
            return super().execute(compile_spec, cases, cancel_check)
        finally:
            self._context.__dict__.clear()

    def _new_compile_cgroup(self) -> ProcessCgroup | None:
        if self.cgroup_manager is None:
            return None
        return self.cgroup_manager.compile_group(
            self._context.job_id, self.limits.compile_memory_mib
        )

    def _new_run_cgroup(self, position: int, memory_mib: int) -> ProcessCgroup | None:
        if self.cgroup_manager is None:
            return None
        return self.cgroup_manager.run_group(
            self._context.owner_public_id,
            self._context.job_id,
            position,
            memory_mib=memory_mib,
            aggregate_mib=MAX_USER_RUNNING_MEMORY_MIB,
        )

    @staticmethod
    def _preexec(memory_mib: int, file_limit: int, group: ProcessCgroup | None):
        attach = group.preexec_attach() if group is not None else None

        def configure() -> None:
            _limit_process(memory_mib, file_limit)
            if attach is not None:
                attach()

        return configure

    @staticmethod
    def _terminate(process: subprocess.Popen, group: ProcessCgroup | None) -> None:
        if group is not None:
            group.kill_all()
        else:
            _kill_group(process)

    def _source_filename(self, compile_spec: CompileSpec) -> str:
        del compile_spec
        return "source.cpp"

    def _compile_with_capacity(
        self, source: Path, binary: Path, compiler: str, job_dir: Path
    ) -> CompileResult:
        with self._compile_semaphore:
            return self._compile(source, binary, compiler, job_dir)

    @staticmethod
    def _base_bwrap_command() -> list[str]:
        command = [
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin",
            "--setenv",
            "HOME",
            "/tmp",
            "--ro-bind",
            "/usr",
            "/usr",
        ]
        for system_path in ("/lib", "/lib64"):
            if Path(system_path).exists():
                command.extend(("--ro-bind", system_path, system_path))
        command.extend(
            (
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--tmpfs",
                "/run",
            )
        )
        return command

    def _compile(
        self, source: Path, binary: Path, compiler: str, job_dir: Path
    ) -> CompileResult:
        stdout_path = job_dir / "compile.stdout"
        stderr_path = job_dir / "compile.stderr"
        command = [
            str(self.bwrap), *self._base_bwrap_command(),
            "--bind", str(job_dir), "/work", "--chdir", "/work", "--",
            *self.COMPILERS[compiler], source.name, "-o", binary.name,
        ]
        started = time.monotonic()
        cgroup = self._new_compile_cgroup()
        file_limit = max(
            self.limits.stdout_limit_bytes, self.limits.stderr_limit_bytes
        ) + 65_536
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    preexec_fn=self._preexec(
                        self.limits.compile_memory_mib, file_limit, cgroup
                    ),
                )
                try:
                    return_code = process.wait(
                        timeout=self.limits.compile_wall_ms / 1000
                    )
                    timed_out = False
                except subprocess.TimeoutExpired:
                    self._terminate(process, cgroup)
                    return_code = process.wait()
                    timed_out = True
            compile_oom = cgroup.oom_killed() if cgroup is not None else False
        finally:
            if cgroup is not None:
                cgroup.close()
        elapsed = int((time.monotonic() - started) * 1000)
        stdout_text, stdout_cut = _read_limited(
            stdout_path, self.limits.stdout_limit_bytes
        )
        stderr_text, stderr_cut = _read_limited(
            stderr_path, self.limits.stderr_limit_bytes
        )
        if timed_out:
            status = "system_error"
            stderr_text = (stderr_text + "\ncompiler wall limit exceeded").strip()
        elif compile_oom:
            status = "system_error"
            stderr_text = (stderr_text + "\ncompiler memory limit exceeded").strip()
        elif return_code != 0:
            status = "compile_error"
        else:
            status = "completed"
        return CompileResult(
            status=status,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=return_code,
            wall_time_ms=elapsed,
            output_truncated=stdout_cut or stderr_cut,
        )

    def _run_case(
        self,
        binary: Path,
        case: CaseSpec,
        position: int,
        job_dir: Path,
        cancel_check: Callable[[], bool],
    ) -> CaseResult:
        prefix = f"case-{position}"
        input_path = job_dir / f"{prefix}.stdin"
        stdout_path = job_dir / f"{prefix}.stdout"
        stderr_path = job_dir / f"{prefix}.stderr"
        input_path.write_text(case.input_text, encoding="utf-8")
        command = [
            str(self.bwrap),
            *self._base_bwrap_command(),
            "--dir",
            "/app",
            "--ro-bind",
            str(binary),
            "/app/main",
            "--ro-bind",
            str(self.sandbox_exec),
            "/sandbox-exec",
            "--chdir",
            "/tmp",
            "--",
            "/sandbox-exec",
            "/app/main",
        ]
        started = time.monotonic()
        peak = 0
        status = "running"
        cgroup = self._new_run_cgroup(position, case.memory_limit_mib)
        with (
            input_path.open("rb") as stdin,
            stdout_path.open("wb") as stdout,
            stderr_path.open("wb") as stderr,
        ):
            process = subprocess.Popen(
                command,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                preexec_fn=self._preexec(
                    case.memory_limit_mib,
                    max(self.limits.stdout_limit_bytes, self.limits.stderr_limit_bytes) + 65_536,
                    cgroup,
                ),
            )
            while process.poll() is None:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                observed = _peak_rss_kib(process.pid)
                if observed is not None:
                    peak = max(peak, observed)
                if cancel_check():
                    status = "cancelled"
                    self._terminate(process, cgroup)
                    break
                if stdout_path.stat().st_size > self.limits.stdout_limit_bytes:
                    status = "output_limit"
                    self._terminate(process, cgroup)
                    break
                if stderr_path.stat().st_size > self.limits.stderr_limit_bytes:
                    status = "output_limit"
                    self._terminate(process, cgroup)
                    break
                if observed is not None and observed > case.memory_limit_mib * 1024:
                    status = "memory_limit"
                    self._terminate(process, cgroup)
                    break
                if elapsed_ms >= case.time_limit_ms:
                    status = "time_limit"
                    self._terminate(process, cgroup)
                    break
                time.sleep(0.005)
            return_code = process.wait()
        run_oom = cgroup.oom_killed() if cgroup is not None else False
        if cgroup is not None:
            cgroup.close()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout_text, stdout_cut = _read_limited(stdout_path, self.limits.stdout_limit_bytes)
        stderr_text, stderr_cut = _read_limited(stderr_path, self.limits.stderr_limit_bytes)
        if status == "running":
            if run_oom:
                status = "memory_limit"
            elif stdout_cut or stderr_cut or return_code == -signal.SIGXFSZ:
                status = "output_limit"
            elif return_code == 0:
                status = "completed"
            elif "bad_alloc" in stderr_text or "cannot allocate memory" in stderr_text.lower():
                status = "memory_limit"
            else:
                status = "runtime_error"
        return CaseResult(
            position=position,
            status=status,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=return_code if return_code >= 0 else None,
            term_signal=-return_code if return_code < 0 else None,
            wall_time_ms=elapsed_ms,
            peak_memory_kib=peak,
            output_truncated=(stdout_cut or stderr_cut or status == "output_limit"),
        )
