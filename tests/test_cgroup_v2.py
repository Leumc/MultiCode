import signal
import uuid

import pytest

from remote_dev.cgroup_v2 import CgroupManager, MemoryCgroupFilesystem


def manager():
    filesystem = MemoryCgroupFilesystem()
    filesystem.mkdir("/delegate")
    return CgroupManager("/delegate", filesystem=filesystem), filesystem


def test_activate_moves_manager_before_enabling_delegated_controllers():
    value, filesystem = manager()
    value.activate()
    assert filesystem.writes("/delegate/manager/cgroup.procs")
    assert filesystem.writes("/delegate/cgroup.subtree_control") == ["+memory +pids"]


def test_compile_group_has_fixed_memory_swap_and_process_limits():
    value, filesystem = manager()
    group = value.compile_group(7, memory_mib=512)

    assert group.path == "/delegate/compile-job-7"
    assert filesystem.value(group.path + "/memory.max") == str(512 * 1024 * 1024)
    assert filesystem.value(group.path + "/memory.swap.max") == "0"
    assert filesystem.value(group.path + "/pids.max") == "64"


def test_run_group_is_nested_under_canonical_user_uuid_and_aggregate_cap():
    value, filesystem = manager()
    owner = str(uuid.uuid4())
    group = value.run_group(owner, job_id=11, case_position=2, memory_mib=64, aggregate_mib=256)

    parent = f"/delegate/users/{owner}"
    assert group.path == parent + "/job-11-case-2"
    assert filesystem.value(parent + "/memory.max") == str(256 * 1024 * 1024)
    assert filesystem.value(group.path + "/memory.max") == str(64 * 1024 * 1024)
    assert filesystem.value(parent + "/pids.max") == "32"
    assert filesystem.value(group.path + "/pids.max") == "8"


def test_rejects_path_injection_and_non_positive_job_coordinates():
    value, _ = manager()
    with pytest.raises(ValueError):
        value.run_group("../escape", 1, 1, 64, 256)
    with pytest.raises(ValueError):
        value.compile_group(0, 512)
    with pytest.raises(ValueError):
        value.run_group(str(uuid.uuid4()), 1, 0, 64, 256)


def test_detects_kernel_oom_kill_counter():
    value, filesystem = manager()
    group = value.compile_group(3, 64)
    filesystem.write(
        group.path + "/memory.events",
        "low 0\nhigh 0\nmax 2\noom 1\noom_kill 1\n",
    )
    assert group.oom_killed() is True


def test_attach_and_kill_uses_cgroup_kill_without_targeting_manager_pid():
    value, filesystem = manager()
    group = value.compile_group(3, 512)
    filesystem.touch(group.path + "/cgroup.kill")

    group.attach_pid(991)
    group.kill_all()

    assert filesystem.writes(group.path + "/cgroup.procs") == ["991"]
    assert filesystem.writes(group.path + "/cgroup.kill") == ["1"]
    assert filesystem.killed == []


def test_kill_falls_back_to_each_member_when_cgroup_kill_is_unavailable():
    value, filesystem = manager()
    group = value.compile_group(4, 512)
    filesystem.set_members(group.path, [201, 202])

    group.kill_all()

    assert filesystem.killed == [(201, signal.SIGKILL), (202, signal.SIGKILL)]
