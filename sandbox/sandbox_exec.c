#define _GNU_SOURCE
#include <errno.h>
#include <seccomp.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <unistd.h>

static const int forbidden_syscalls[] = {
    SCMP_SYS(clone),
    SCMP_SYS(clone3),
    SCMP_SYS(fork),
    SCMP_SYS(vfork),
    SCMP_SYS(socket),
    SCMP_SYS(socketpair),
    SCMP_SYS(connect),
    SCMP_SYS(bind),
    SCMP_SYS(listen),
    SCMP_SYS(accept),
    SCMP_SYS(accept4),
    SCMP_SYS(mount),
    SCMP_SYS(umount2),
    SCMP_SYS(unshare),
    SCMP_SYS(setns),
    SCMP_SYS(ptrace),
    SCMP_SYS(bpf),
    SCMP_SYS(perf_event_open),
    SCMP_SYS(keyctl),
    SCMP_SYS(io_uring_setup),
    SCMP_SYS(io_uring_enter),
    SCMP_SYS(io_uring_register),
};

static int install_filter(void) {
    scmp_filter_ctx context = seccomp_init(SCMP_ACT_ALLOW);
    if (context == NULL) {
        errno = ENOMEM;
        return -1;
    }

    int result = 0;
    for (size_t index = 0;
         index < sizeof(forbidden_syscalls) / sizeof(forbidden_syscalls[0]);
         ++index) {
        result = seccomp_rule_add(
            context, SCMP_ACT_ERRNO(EPERM), forbidden_syscalls[index], 0
        );
        if (result < 0) {
            errno = -result;
            seccomp_release(context);
            return -1;
        }
    }

    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        seccomp_release(context);
        return -1;
    }
    result = seccomp_load(context);
    seccomp_release(context);
    if (result < 0) {
        errno = -result;
        return -1;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fputs("usage: sandbox-exec PROGRAM [ARGS...]\n", stderr);
        return 125;
    }
    if (install_filter() != 0) {
        perror("install seccomp filter");
        return 125;
    }
    execv(argv[1], &argv[1]);
    perror("execv sandbox program");
    return 126;
}
