import os
import pathlib
import subprocess
import threading

import pytest

from remote_dev.runner import (
    CaseSpec,
    CompileSpec,
    CompileResult,
    RunnerLimits,
    SandboxedCppRunner,
)


@pytest.fixture(scope="module")
def sandbox_exec(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    root = pathlib.Path(__file__).parents[1]
    output = tmp_path_factory.mktemp("sandbox-launcher") / "sandbox-exec"
    subprocess.run(
        [
            "gcc",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(root / "sandbox/sandbox_exec.c"),
            "-lseccomp",
            "-o",
            str(output),
        ],
        check=True,
    )
    return output


def runner(tmp_path: pathlib.Path, sandbox_exec: pathlib.Path) -> SandboxedCppRunner:
    return SandboxedCppRunner(
        tmp_path / "jobs",
        RunnerLimits(
            compile_wall_ms=30_000,
            compile_memory_mib=512,
            stdout_limit_bytes=32_768,
            stderr_limit_bytes=16_384,
            total_wall_ms=10_000,
        ),
        sandbox_exec=sandbox_exec,
    )


def test_sandbox_runner_places_compile_and_cases_in_cgroups(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    events = []

    class Group:
        def __init__(self, name): self.name = name
        def preexec_attach(self):
            events.append(("attach", self.name))
            return lambda: None
        def kill_all(self): events.append(("kill", self.name))
        def oom_killed(self): return False
        def close(self): events.append(("close", self.name))

    class Manager:
        def compile_group(self, job_id, memory_mib):
            events.append(("compile", job_id, memory_mib))
            return Group("compile")
        def run_group(self, owner, job_id, position, *, memory_mib, aggregate_mib):
            events.append(("run", owner, job_id, position, memory_mib, aggregate_mib))
            return Group(f"case-{position}")

    value = SandboxedCppRunner(
        tmp_path / "jobs",
        RunnerLimits(30_000, 512, 32_768, 16_384, 10_000),
        sandbox_exec=sandbox_exec,
        cgroup_manager=Manager(),
    )
    owner = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result = value.execute(
        CompileSpec(
            "ignored.cpp", "#include <iostream>\nint main(){std::cout << 7;}",
            "gcc-14-gnu++17", job_id=7, owner_public_id=owner,
        ),
        [CaseSpec("", 1_000, 64), CaseSpec("", 1_000, 64)],
    )
    assert result.status == "completed"
    assert ("compile", 7, 512) in events
    assert ("run", owner, 7, 1, 64, 256) in events
    assert ("run", owner, 7, 2, 64, 256) in events
    assert events.count(("close", "compile")) == 1
    assert events.count(("close", "case-1")) == 1
    assert events.count(("close", "case-2")) == 1


def test_cgroup_oom_is_reported_as_memory_limit(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    class Group:
        def __init__(self, oom=False): self.oom = oom
        def preexec_attach(self): return lambda: None
        def kill_all(self): pass
        def oom_killed(self): return self.oom
        def close(self): pass

    class Manager:
        def compile_group(self, job_id, memory_mib): return Group(False)
        def run_group(self, owner, job_id, position, *, memory_mib, aggregate_mib):
            return Group(True)

    value = SandboxedCppRunner(
        tmp_path / "jobs",
        RunnerLimits(30_000, 512, 32_768, 16_384, 10_000),
        sandbox_exec=sandbox_exec,
        cgroup_manager=Manager(),
    )
    result = value.execute(
        CompileSpec(
            "main.cpp", "int main(){return 0;}", "gcc-14-gnu++17",
            job_id=8,
            owner_public_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        ),
        [CaseSpec("", 1_000, 64)],
    )
    assert result.cases[0].status == "memory_limit"


def test_sandboxed_runner_compiles_fixed_gnu17_and_runs_normally(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    source = r"""
#include <bits/stdc++.h>
int main() { long long value; std::cin >> value; std::cout << value * 2 << '\n'; }
"""
    result = runner(tmp_path, sandbox_exec).execute(
        CompileSpec("main.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("21\n", 1_000, 64)],
    )

    assert result.status == "completed"
    assert result.compile.status == "completed"
    assert result.cases[0].status == "completed"
    assert result.cases[0].stdout == "42\n"


def test_sandboxed_runner_uses_source_snapshot_not_submitted_filename(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    jobs = tmp_path / "jobs"
    result = runner(tmp_path, sandbox_exec).execute(
        CompileSpec("../escaped.cpp", "int main() { return 0; }", "gcc-14-gnu++17"),
        [CaseSpec("", 1_000, 64)],
    )

    assert result.status == "completed"
    assert list(jobs.iterdir()) == []


def test_sandboxed_runner_denies_fork_and_socket_with_eperm(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    source = r"""
#include <cerrno>
#include <iostream>
#include <sys/socket.h>
#include <unistd.h>
int main() {
    errno = 0;
    pid_t child = fork();
    int fork_errno = errno;
    errno = 0;
    int descriptor = socket(AF_INET, SOCK_STREAM, 0);
    int socket_errno = errno;
    std::cout << child << ' ' << fork_errno << ' '
              << descriptor << ' ' << socket_errno << '\n';
    return child == -1 && fork_errno == EPERM
        && descriptor == -1 && socket_errno == EPERM ? 0 : 9;
}
"""
    result = runner(tmp_path, sandbox_exec).execute(
        CompileSpec("attack.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 1_000, 64)],
    )

    assert result.cases[0].status == "completed"
    assert result.cases[0].stdout == "-1 1 -1 1\n"


def test_sandboxed_runner_has_private_network_namespace(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    host_net_namespace = os.readlink("/proc/self/ns/net")
    source = r"""
#include <fstream>
#include <iostream>
#include <unistd.h>
int main() {
    char target[256] = {};
    ssize_t size = readlink("/proc/self/ns/net", target, sizeof(target) - 1);
    if (size < 0) return 2;
    std::cout << target << '\n';
    std::ifstream devices("/proc/net/dev");
    std::cout << devices.rdbuf();
}
"""
    result = runner(tmp_path, sandbox_exec).execute(
        CompileSpec("network.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 1_000, 64)],
    )

    case = result.cases[0]
    assert case.status == "completed"
    assert host_net_namespace not in case.stdout
    assert "lo:" in case.stdout
    assert "eth0:" not in case.stdout


def test_sandboxed_runner_hides_host_paths_and_makes_system_read_only(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    source = r"""
#include <cerrno>
#include <fcntl.h>
#include <iostream>
#include <unistd.h>
int main() {
    int visible = access("/host-private/project/pyproject.toml", F_OK);
    errno = 0;
    int fd = open("/usr/rdp-sandbox-write", O_WRONLY | O_CREAT, 0600);
    int write_errno = errno;
    if (fd >= 0) close(fd);
    std::cout << visible << ' ' << fd << ' ' << write_errno << '\n';
    return visible == -1 && fd == -1 && write_errno == EROFS ? 0 : 8;
}
"""
    result = runner(tmp_path, sandbox_exec).execute(
        CompileSpec("filesystem.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 1_000, 64)],
    )

    assert result.cases[0].status == "completed"
    assert result.cases[0].stdout == "-1 -1 30\n"


def test_sandboxed_runner_gives_every_case_a_fresh_private_tmp(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    source = r"""
#include <fstream>
#include <iostream>
int main() {
    std::ifstream before("/tmp/case-marker");
    std::cout << (before.good() ? "dirty" : "fresh") << '\n';
    std::ofstream marker("/tmp/case-marker");
    marker << "created";
}
"""
    result = runner(tmp_path, sandbox_exec).execute(
        CompileSpec("fresh.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 1_000, 64), CaseSpec("", 1_000, 64)],
    )

    assert [case.status for case in result.cases] == ["completed", "completed"]
    assert [case.stdout for case in result.cases] == ["fresh\n", "fresh\n"]


def test_sandboxed_runner_unshares_user_mount_and_pid_namespaces(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    host_namespaces = {
        namespace: os.readlink(f"/proc/self/ns/{namespace}")
        for namespace in ("user", "mnt", "pid")
    }
    source = r"""
#include <iostream>
#include <unistd.h>
int main() {
    for (const char *name : {"user", "mnt", "pid"}) {
        std::string path = std::string("/proc/self/ns/") + name;
        char target[256] = {};
        ssize_t size = readlink(path.c_str(), target, sizeof(target) - 1);
        if (size < 0) return 2;
        std::cout << name << '=' << target << '\n';
    }
}
"""
    result = runner(tmp_path, sandbox_exec).execute(
        CompileSpec("namespaces.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 1_000, 64)],
    )

    case = result.cases[0]
    assert case.status == "completed"
    assert all(host_namespace not in case.stdout for host_namespace in host_namespaces.values())


def test_sandboxed_runner_leaves_no_files_or_processes_after_timeout(
    tmp_path: pathlib.Path, sandbox_exec: pathlib.Path
):
    jobs = tmp_path / "jobs"
    unique_token = f"rdp-residue-{os.getpid()}-{tmp_path.name}"
    source = f'int main() {{ for (;;) {{}} }} // {unique_token}'
    result = runner(tmp_path, sandbox_exec).execute(
        CompileSpec("timeout.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 100, 64)],
    )

    assert result.cases[0].status == "time_limit"
    assert list(jobs.iterdir()) == []
    matching_processes = []
    for command_line in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            text = command_line.read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if unique_token in text:
            matching_processes.append(command_line.parent.name)
    assert matching_processes == []


def test_shared_sandboxed_runner_allows_only_one_compile_at_a_time(tmp_path):
    entered = 0
    maximum_entered = 0
    state_lock = threading.Lock()
    first_entered = threading.Event()
    second_entered = threading.Event()
    release = threading.Event()

    class ProbeSandbox(SandboxedCppRunner):
        def _compile(self, source, binary, compiler, job_dir):
            nonlocal entered, maximum_entered
            with state_lock:
                entered += 1
                maximum_entered = max(maximum_entered, entered)
                if entered == 1:
                    first_entered.set()
                else:
                    second_entered.set()
            release.wait(timeout=5)
            with state_lock:
                entered -= 1
            return CompileResult("compile_error", stderr="probe")

    limits = RunnerLimits(1_000, 64, 1_024, 1_024, 1_000)
    shared_runner = ProbeSandbox(
        tmp_path / "jobs", limits, sandbox_exec="/bin/true", bwrap="/bin/true"
    )

    def execute():
        shared_runner.execute(
            CompileSpec("main.cpp", "int main(){}", "gcc-14-gnu++17"), []
        )

    threads = [threading.Thread(target=execute) for _ in range(2)]
    threads[0].start()
    assert first_entered.wait(timeout=2)
    threads[1].start()
    try:
        assert not second_entered.wait(timeout=0.2)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum_entered == 1
