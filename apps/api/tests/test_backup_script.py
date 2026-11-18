"""Backup publication/permissions and retention on failure; provider CLI is stubbed."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "infra/backup/postgres.sh"


@pytest.mark.parametrize("failed_step", ["dump", "verify", "none"])
def test_backup_publishes_private_archive_only_after_verification(tmp_path, failed_step):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("""#!/bin/bash
if [[ "$*" == *pg_dump* ]]; then
  printf 'synthetic archive bytes'
  [[ "$FAIL_STEP" != dump ]]
elif [[ "$*" == *pg_restore* ]]; then
  cat >/dev/null
  [[ "$FAIL_STEP" != verify ]]
else
  exit 2
fi
""")
    docker.chmod(0o700)
    storage = tmp_path / "backups"
    storage.mkdir()
    previous = storage / "postgres-old.dump"
    previous.write_text("previous working backup")
    os.utime(previous, (1, 1))
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DOSADASH_BACKUP_DIR": str(storage),
            "FAIL_STEP": failed_step,
        },
    )
    assert stat.S_IMODE(storage.stat().st_mode) == 0o700
    assert not list(storage.glob(".postgres.*"))
    if failed_step == "none":
        assert result.returncode == 0, result.stderr
        assert not previous.exists()
        (archive,) = storage.glob("postgres-*.dump")
        assert archive.read_text() == "synthetic archive bytes"
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600
        assert Path(str(archive) + ".sha256").exists()
    else:
        assert result.returncode != 0
        assert previous.read_text() == "previous working backup"
        assert list(storage.glob("postgres-*.dump")) == [previous]
