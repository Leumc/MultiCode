"""Create portable, fixed-capacity ext4 workspace images."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import uuid


def _canonical_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("workspace identity must be a canonical UUID") from error
    canonical = str(parsed)
    if value != canonical:
        raise ValueError("workspace identity must be a canonical UUID")
    return canonical


def create_workspace_image(
    image_root: str | Path,
    public_id: str,
    *,
    size_mib: int = 1024,
) -> Path:
    """Create one sparse ext4 image without overwriting existing user data."""
    identity = _canonical_uuid(public_id)
    if not 64 <= size_mib <= 16_384:
        raise ValueError("workspace image size must be between 64 and 16384 MiB")

    root = Path(image_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    image = root / f"{identity}.img"
    descriptor = os.open(image, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.ftruncate(descriptor, size_mib * 1024 * 1024)
    finally:
        os.close(descriptor)

    try:
        with tempfile.TemporaryDirectory(prefix="workspace-root-", dir=root) as staging:
            os.chmod(staging, 0o777)
            subprocess.run(
                [
                    "/usr/sbin/mkfs.ext4",
                    "-q",
                    "-F",
                    "-L",
                    f"rdp-{identity[:8]}",
                    "-d",
                    staging,
                    str(image),
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                [
                    "/usr/sbin/debugfs",
                    "-w",
                    "-R",
                    "set_inode_field / mode 040777",
                    str(image),
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        os.chmod(image, 0o600)
        return image
    except BaseException:
        image.unlink(missing_ok=True)
        raise
