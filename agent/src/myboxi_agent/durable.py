"""Files that survive a pulled battery (CLAUDE.md rule 5): data on disk before the rename,
the rename on disk before the file is registered."""

from __future__ import annotations

import os
from pathlib import Path


def fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace(src: Path, dest: Path) -> None:
    """Move ``src`` to ``dest`` atomically and durably (same file system)."""
    fsync_file(src)
    src.replace(dest)
    fsync_dir(dest.parent)
