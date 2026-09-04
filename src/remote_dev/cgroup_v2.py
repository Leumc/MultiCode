"""Minimal cgroup v2 lifecycle management for sandbox subprocesses."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import signal
from typing import Protocol
import uuid


class CgroupFilesystem(Protocol):
    def mkdir(self, path: str) -> None: ...
    def write(self, path: str, value: str) -> None: ...
    def exists(self, path: str) -> bool: ...
    def read(self, path: str) -> str: ...
    def members(self, path: str) -> list[int]: ...
    def kill(self, pid: int, sig: int) -> None: ...
    def rmdir(self, path: str) -> None: ...


class RealCgroupFilesystem:
    def mkdir(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def write(self, path: str, value: str) -> None:
        with Path(path).open("w", encoding="ascii") as handle:
            handle.write(value)

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def read(self, path: str) -> str:
        return Path(path).read_text(encoding="ascii")

    def members(self, path: str) -> list[int]:
        try:
            return [int(value) for value in Path(path, "cgroup.procs").read_text().split()]
        except FileNotFoundError:
            return []

    def kill(self, pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass

    def rmdir(self, path: str) -> None:
        try:
            Path(path).rmdir()
        except (FileNotFoundError, OSError):
            pass


@dataclass
class MemoryCgroupFilesystem:
    """Deterministic test backend; never interacts with the host cgroup tree."""

    directories: set[str] = field(default_factory=set)
    controls: dict[str, str] = field(default_factory=dict)
    write_log: dict[str, list[str]] = field(default_factory=dict)
    member_map: dict[str, list[int]] = field(default_factory=dict)
    killed: list[tuple[int, int]] = field(default_factory=list)

    def mkdir(self, path: str) -> None:
        current = Path(path)
        for item in [current, *current.parents]:
            self.directories.add(str(item))
            if str(item) == "/":
                break

    def write(self, path: str, value: str) -> None:
        self.write_log.setdefault(path, []).append(value)
        self.controls[path] = value

    def exists(self, path: str) -> bool:
        return path in self.directories or path in self.controls

    def read(self, path: str) -> str:
        if path not in self.controls:
            raise FileNotFoundError(path)
        return self.controls[path]

    def members(self, path: str) -> list[int]:
        return list(self.member_map.get(path, []))

    def kill(self, pid: int, sig: int) -> None:
        self.killed.append((pid, sig))

    def rmdir(self, path: str) -> None:
        self.directories.discard(path)

    def touch(self, path: str) -> None:
        self.controls[path] = ""

    def value(self, path: str) -> str | None:
        return self.controls.get(path)

    def writes(self, path: str) -> list[str]:
        return list(self.write_log.get(path, []))

    def set_members(self, path: str, pids: list[int]) -> None:
        self.member_map[path] = list(pids)


@dataclass(frozen=True)
class ProcessCgroup:
    path: str
    filesystem: CgroupFilesystem

    def attach_pid(self, pid: int) -> None:
        if pid < 0:
            raise ValueError("pid must be zero (self) or positive")
        self.filesystem.write(f"{self.path}/cgroup.procs", str(pid))

    def preexec_attach(self):
        """Return a child-side hook that moves itself before exec creates descendants."""
        return lambda: self.attach_pid(0)

    def oom_killed(self) -> bool:
        try:
            events = self.filesystem.read(f"{self.path}/memory.events")
        except (FileNotFoundError, PermissionError):
            return False
        values = {}
        for line in events.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].isdigit():
                values[fields[0]] = int(fields[1])
        return values.get("oom_kill", 0) > 0

    def kill_all(self) -> None:
        kill_path = f"{self.path}/cgroup.kill"
        if self.filesystem.exists(kill_path):
            self.filesystem.write(kill_path, "1")
            return
        for pid in self.filesystem.members(self.path):
            self.filesystem.kill(pid, signal.SIGKILL)

    def close(self) -> None:
        self.kill_all()
        self.filesystem.rmdir(self.path)


class CgroupManager:
    def __init__(self, root: str | Path, *, filesystem: CgroupFilesystem | None = None):
        self.root = str(Path(root))
        self.filesystem = filesystem or RealCgroupFilesystem()

    @classmethod
    def discover(cls, mount: str | Path = "/sys/fs/cgroup") -> "CgroupManager":
        relative = None
        for line in Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines():
            if line.startswith("0::"):
                relative = line[3:].lstrip("/")
                break
        if relative is None:
            raise RuntimeError("process is not running in a cgroup v2 hierarchy")
        root = Path(mount) / relative
        if not (root / "cgroup.controllers").exists():
            raise RuntimeError("worker cgroup is not delegated")
        return cls(root)

    def activate(self) -> None:
        """Move the worker into a manager leaf, then delegate job controllers."""
        manager = f"{self.root}/manager"
        self.filesystem.mkdir(manager)
        self.filesystem.write(f"{manager}/cgroup.procs", str(os.getpid()))
        self._enable(self.root)

    def _enable(self, path: str) -> None:
        self.filesystem.write(f"{path}/cgroup.subtree_control", "+memory +pids")

    @staticmethod
    def _positive(value: int, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} must be a positive integer")
        return value

    @staticmethod
    def _owner(value: str) -> str:
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("owner must be a canonical UUID") from None
        return value

    def _group(self, path: str, memory_mib: int, pids: int) -> ProcessCgroup:
        self._positive(memory_mib, "memory_mib")
        self._positive(pids, "pids")
        self.filesystem.mkdir(path)
        self.filesystem.write(f"{path}/memory.max", str(memory_mib * 1024 * 1024))
        self.filesystem.write(f"{path}/memory.swap.max", "0")
        self.filesystem.write(f"{path}/pids.max", str(pids))
        return ProcessCgroup(path, self.filesystem)

    def compile_group(self, job_id: int, memory_mib: int) -> ProcessCgroup:
        job_id = self._positive(job_id, "job_id")
        return self._group(f"{self.root}/compile-job-{job_id}", memory_mib, 64)

    def run_group(
        self, owner: str, job_id: int, case_position: int,
        memory_mib: int, aggregate_mib: int = 256,
    ) -> ProcessCgroup:
        owner = self._owner(owner)
        job_id = self._positive(job_id, "job_id")
        case_position = self._positive(case_position, "case_position")
        memory_mib = self._positive(memory_mib, "memory_mib")
        aggregate_mib = self._positive(aggregate_mib, "aggregate_mib")
        if memory_mib > aggregate_mib:
            raise ValueError("case memory exceeds aggregate memory reservation")
        users = f"{self.root}/users"
        self.filesystem.mkdir(users)
        self._enable(users)
        parent = f"{users}/{owner}"
        self._group(parent, aggregate_mib, 32)
        self._enable(parent)
        return self._group(f"{parent}/job-{job_id}-case-{case_position}", memory_mib, 8)
