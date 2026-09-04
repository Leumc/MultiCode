import pathlib

from remote_dev.runner import CaseSpec, CompileSpec, LocalCppRunner, RunnerLimits


def runner(tmp_path: pathlib.Path, **overrides):
    values = {
        "compile_wall_ms": 10_000,
        "compile_memory_mib": 512,
        "stdout_limit_bytes": 32_768,
        "stderr_limit_bytes": 16_384,
        "total_wall_ms": 10_000,
    }
    values.update(overrides)
    return LocalCppRunner(tmp_path, RunnerLimits(**values))


def test_compiles_once_and_runs_each_input_in_a_fresh_process(tmp_path):
    source = r"""
#include <bits/stdc++.h>
int main() { long long x; std::cin >> x; std::cout << x * 2 << '\n'; }
"""
    result = runner(tmp_path).execute(
        CompileSpec("main.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("2\n", 1000, 64), CaseSpec("21\n", 1000, 64)],
    )
    assert result.status == "completed"
    assert [case.status for case in result.cases] == ["completed", "completed"]
    assert [case.stdout for case in result.cases] == ["4\n", "42\n"]
    assert all(case.wall_time_ms >= 0 for case in result.cases)
    assert all(case.peak_memory_kib is not None for case in result.cases)


def test_compile_error_returns_original_compiler_diagnostics(tmp_path):
    result = runner(tmp_path).execute(
        CompileSpec("broken.cpp", "int main( {", "gcc-14-gnu++17"),
        [CaseSpec("", 1000, 64)],
    )
    assert result.status == "compile_error"
    assert result.compile.exit_code != 0
    assert "error:" in result.compile.stderr
    assert result.cases == []


def test_wall_timeout_kills_program_and_reports_tle(tmp_path):
    source = "int main(){for(;;){} }"
    result = runner(tmp_path).execute(
        CompileSpec("loop.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 150, 64)],
    )
    case = result.cases[0]
    assert case.status == "time_limit"
    assert case.wall_time_ms >= 100
    assert case.wall_time_ms < 2000


def test_stdout_limit_kills_program_and_keeps_truncated_output(tmp_path):
    source = r"""
#include <iostream>
int main(){for(;;) std::cout << "0123456789abcdef";}
"""
    result = runner(tmp_path, stdout_limit_bytes=4096).execute(
        CompileSpec("output.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 2000, 64)],
    )
    case = result.cases[0]
    assert case.status == "output_limit"
    assert case.output_truncated is True
    assert len(case.stdout.encode()) <= 4096


def test_memory_limit_reports_mle_instead_of_generic_re(tmp_path):
    source = r"""
#include <bits/stdc++.h>
int main(){ std::vector<char> x(256ULL * 1024 * 1024); std::cout << x[0]; }
"""
    result = runner(tmp_path).execute(
        CompileSpec("memory.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 2000, 32)],
    )
    case = result.cases[0]
    assert case.status == "memory_limit"
    assert case.stderr


def test_runtime_error_preserves_exit_code_and_stderr(tmp_path):
    source = r"""
#include <iostream>
int main(){std::cerr << "raw failure"; return 7;}
"""
    result = runner(tmp_path).execute(
        CompileSpec("failure.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 1000, 64)],
    )
    case = result.cases[0]
    assert case.status == "runtime_error"
    assert case.exit_code == 7
    assert case.stderr == "raw failure"


def test_running_job_can_be_cancelled_and_remaining_cases_are_cancelled(tmp_path):
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 5

    result = runner(tmp_path).execute(
        CompileSpec("main.cpp", "int main(){for(;;){}}", "gcc-14-gnu++17"),
        [CaseSpec("", 5_000, 64), CaseSpec("", 5_000, 64)],
        cancel_check=cancelled,
    )
    assert result.status == "cancelled"
    assert [case.status for case in result.cases] == ["cancelled", "cancelled"]
    assert result.cases[0].wall_time_ms < 1_000


def test_total_submission_wall_limit_cancels_remaining_cases(tmp_path):
    source = "int main(){for(;;){} }"
    result = runner(tmp_path, total_wall_ms=250).execute(
        CompileSpec("loop.cpp", source, "gcc-14-gnu++17"),
        [CaseSpec("", 200, 64), CaseSpec("", 200, 64), CaseSpec("", 200, 64)],
    )
    assert result.cases[0].status == "time_limit"
    assert any(case.status == "cancelled" for case in result.cases[1:])
