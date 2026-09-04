from __future__ import annotations

import subprocess

from remote_dev.cgroup_v2 import CgroupManager


manager = CgroupManager.discover()
manager.activate()
group = manager.compile_group(9001, 64)
process = subprocess.Popen(
    ["/usr/bin/sleep", "30"],
    preexec_fn=group.preexec_attach(),
)
try:
    members = group.filesystem.members(group.path)
    if process.pid not in members:
        raise SystemExit(f"child not attached: pid={process.pid}, members={members}")
    group.kill_all()
    code = process.wait(timeout=5)
    if code == 0:
        raise SystemExit("cgroup kill did not terminate child")
    print(f"CGROUP_PROBE_OK path={group.path} child_exit={code}")
finally:
    if process.poll() is None:
        process.kill()
        process.wait()
    group.close()
