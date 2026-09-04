import pathlib
import subprocess


def build(tmp_path: pathlib.Path, name: str, source: str) -> pathlib.Path:
    path = tmp_path / f"{name}.c"
    binary = tmp_path / name
    path.write_text(source, encoding="utf-8")
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", str(path), "-o", str(binary)], check=True)
    return binary


def test_seccomp_launcher_allows_normal_program_and_denies_process_and_network(tmp_path):
    root = pathlib.Path(__file__).parents[1]
    launcher = tmp_path / "sandbox-exec"
    subprocess.run([
        "gcc", "-O2", "-Wall", "-Wextra", "-Werror",
        str(root / "sandbox/sandbox_exec.c"), "-lseccomp", "-o", str(launcher),
    ], check=True)
    assert "libseccomp.so" in subprocess.check_output(
        ["ldd", str(launcher)], text=True
    )

    normal = build(tmp_path, "normal", '#include <stdio.h>\nint main(){puts("NORMAL_OK");}\n')
    normal_result = subprocess.run([launcher, normal], text=True, capture_output=True)
    assert normal_result.returncode == 0
    assert normal_result.stdout == "NORMAL_OK\n"

    forbidden = build(tmp_path, "forbidden", r'''
#include <errno.h>
#include <stdio.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>
int main(void) {
    errno = 0; pid_t child = fork(); int fork_errno = errno;
    errno = 0; long clone_result = syscall(SYS_clone, 0, 0, 0, 0, 0); int clone_errno = errno;
    errno = 0; int fd = socket(AF_INET, SOCK_STREAM, 0); int socket_errno = errno;
    errno = 0; long ring_result = syscall(SYS_io_uring_setup, 1, 0); int ring_errno = errno;
    printf("fork=%ld fork_errno=%d clone=%ld clone_errno=%d socket=%d socket_errno=%d ring=%ld ring_errno=%d\n",
           (long)child, fork_errno, clone_result, clone_errno, fd, socket_errno,
           ring_result, ring_errno);
    return (child == -1 && fork_errno == EPERM
            && clone_result == -1 && clone_errno == EPERM
            && fd == -1 && socket_errno == EPERM
            && ring_result == -1 && ring_errno == EPERM) ? 0 : 9;
}
''')
    denied = subprocess.run([launcher, forbidden], text=True, capture_output=True)
    assert denied.returncode == 0, denied.stderr
    assert "fork=-1 fork_errno=1 clone=-1 clone_errno=1 socket=-1 socket_errno=1 ring=-1 ring_errno=1" in denied.stdout
