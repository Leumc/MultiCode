import os
import subprocess
import uuid

import pytest

from remote_dev.workspace_image import create_workspace_image


def test_creates_sparse_ext4_workspace_image_named_by_uuid(tmp_path):
    public_id = str(uuid.uuid4())
    image = create_workspace_image(tmp_path, public_id, size_mib=64)

    assert image == tmp_path / f"{public_id}.img"
    assert image.stat().st_size == 64 * 1024 * 1024
    assert image.stat().st_blocks * 512 < image.stat().st_size
    assert image.stat().st_mode & 0o777 == 0o600

    filesystem = subprocess.run(
        ["/usr/sbin/blkid", "-o", "value", "-s", "TYPE", str(image)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert filesystem == "ext4"

    root_inode = subprocess.run(
        ["/usr/sbin/debugfs", "-R", "stat /", str(image)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Mode:  0777" in root_inode


def test_workspace_image_rejects_non_uuid_identity(tmp_path):
    with pytest.raises(ValueError, match="canonical UUID"):
        create_workspace_image(tmp_path, "../../other-user", size_mib=64)


def test_workspace_image_creation_never_overwrites_existing_data(tmp_path):
    public_id = str(uuid.uuid4())
    image = tmp_path / f"{public_id}.img"
    image.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        create_workspace_image(tmp_path, public_id, size_mib=64)
    assert image.read_bytes() == b"existing"
