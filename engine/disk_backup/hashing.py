from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, Callable

from .config import BUFFER_SIZE

ProgressCallback = Callable[[int], None]


def sha256_stream(stream: BinaryIO, progress: ProgressCallback | None = None) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(BUFFER_SIZE):
        digest.update(chunk)
        if progress:
            progress(len(chunk))
    return digest.hexdigest()


def sha256_file(path: Path, progress: ProgressCallback | None = None) -> str:
    with path.open("rb", buffering=0) as stream:
        return sha256_stream(stream, progress)


def copy_and_hash(
    source: Path,
    temporary_target: Path,
    progress: ProgressCallback | None = None,
) -> str:
    """Copy once while hashing, flush to disk, and leave final rename to the caller."""
    digest = hashlib.sha256()
    with source.open("rb", buffering=0) as src, temporary_target.open("xb", buffering=0) as dst:
        while chunk := src.read(BUFFER_SIZE):
            dst.write(chunk)
            digest.update(chunk)
            if progress:
                progress(len(chunk))
        dst.flush()
        os_fsync(dst)
    return digest.hexdigest()


def os_fsync(stream: BinaryIO) -> None:
    import os

    os.fsync(stream.fileno())


def publish_without_overwrite(temporary: Path, target: Path) -> None:
    """Publish an owned temporary file, refusing a racing destination conflict."""
    import os
    if os.name == 'nt':
        os.rename(temporary, target)  # Windows rename fails if the target exists.
    else:
        os.link(temporary, target)  # Exclusive creation, unlike POSIX rename.
        temporary.unlink()
