from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import default_workers
from .database import Catalog, utc_now
from .devices import Device, iter_files
from .hashing import sha256_file
from .media import classify_name


@dataclass(slots=True)
class ScanEstimate:
    files: int
    bytes: int
    seconds: float
    known_from_cache: bool


@dataclass(slots=True)
class ScanResult:
    scan_id: int
    files: int
    bytes: int
    hashed_files: int
    hashed_bytes: int
    removed_entries: int
    errors: list[str]
    elapsed_seconds: float


def estimate_scan(catalog: Catalog, device: Device, full: bool) -> ScanEstimate:
    row = catalog.connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(size),0) FROM files WHERE volume_id=?", (device.id,)
    ).fetchone()
    count, byte_count = int(row[0]), int(row[1])
    if count:
        operation = "full_hash_bytes" if full else "incremental_files"
        units = byte_count if full else count
        fallback = 80 * 1024 * 1024 if full else 20_000
        return ScanEstimate(count, byte_count, units / catalog.estimate_rate(operation, fallback), True)
    used = max(0, (device.total_bytes or 0) - (device.free_bytes or 0))
    rate = catalog.estimate_rate("full_hash_bytes", 80 * 1024 * 1024)
    return ScanEstimate(0, used, used / rate if used else 0, False)


def index_volume(
    catalog: Catalog,
    device: Device,
    *,
    full: bool = False,
    excluded_roots: tuple[Path, ...] = (),
    workers: int | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> ScanResult:
    if not device.is_filesystem:
        raise ValueError("MTP devices can be backed up but cannot be indexed in place reliably.")
    root = Path(device.root).resolve()
    catalog.upsert_volume(device.id, device.label, str(root), device.kind)
    mode = "full" if full else "incremental"
    started = time.monotonic()
    with catalog.transaction() as connection:
        cursor = connection.execute(
            "INSERT INTO scans(volume_id,mode,started_at,status) VALUES(?,?,?,'running')",
            (device.id, mode, utc_now()),
        )
        scan_id = int(cursor.lastrowid)

    existing = {
        row["relative_path"]: row
        for row in catalog.connection.execute(
            "SELECT relative_path,size,mtime_ns,sha256 FROM files WHERE volume_id=?", (device.id,)
        )
    }
    errors: list[str] = []
    total_files = total_bytes = hashed_files = hashed_bytes = 0
    pending: dict[concurrent.futures.Future[str], tuple[str, str, int, int]] = {}
    write_buffer: list[tuple[object, ...]] = []
    max_workers = workers or default_workers()

    def flush() -> None:
        if not write_buffer:
            return
        with catalog.transaction() as connection:
            connection.executemany(
                """INSERT INTO files(volume_id,relative_path,name,size,mtime_ns,sha256,last_seen_scan,last_verified_at,extension,category)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(volume_id,relative_path) DO UPDATE SET
                name=excluded.name,size=excluded.size,mtime_ns=excluded.mtime_ns,
                sha256=excluded.sha256,last_seen_scan=excluded.last_seen_scan,
                last_verified_at=excluded.last_verified_at,extension=excluded.extension,
                category=excluded.category""",
                write_buffer,
            )
        write_buffer.clear()

    def store(relative: str, name: str, size: int, mtime_ns: int, digest: str | None) -> None:
        extension, category = classify_name(name)
        write_buffer.append(
            (device.id, relative, name, size, mtime_ns, digest, scan_id,
             utc_now() if digest else None, extension, category)
        )
        if len(write_buffer) >= 250:
            flush()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for path in iter_files(root, excluded_roots):
                try:
                    stat = path.stat()
                    relative = path.relative_to(root).as_posix()
                    size, mtime_ns = stat.st_size, stat.st_mtime_ns
                    total_files += 1
                    total_bytes += size
                    old = existing.get(relative)
                    unchanged = bool(old and old["size"] == size and old["mtime_ns"] == mtime_ns and old["sha256"])
                    if unchanged and not full:
                        store(relative, path.name, size, mtime_ns, str(old["sha256"]))
                    else:
                        future = executor.submit(sha256_file, path)
                        pending[future] = (relative, path.name, size, mtime_ns)
                    if len(pending) >= max_workers * 4:
                        done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                        for future in done:
                            relative2, name2, size2, mtime2 = pending.pop(future)
                            try:
                                store(relative2, name2, size2, mtime2, future.result())
                                hashed_files += 1
                                hashed_bytes += size2
                            except Exception as exc:
                                errors.append(f"{relative2}: {exc}")
                    if progress:
                        progress(total_files, total_bytes, hashed_bytes)
                except (OSError, PermissionError) as exc:
                    errors.append(f"{path}: {exc}")
            for future in concurrent.futures.as_completed(pending):
                relative, name, size, mtime_ns = pending[future]
                try:
                    store(relative, name, size, mtime_ns, future.result())
                    hashed_files += 1
                    hashed_bytes += size
                except Exception as exc:
                    errors.append(f"{relative}: {exc}")
                if progress:
                    progress(total_files, total_bytes, hashed_bytes)

        flush()
        with catalog.transaction() as connection:
            removed = 0 if errors else connection.execute(
                "DELETE FROM files WHERE volume_id=? AND last_seen_scan<>?", (device.id, scan_id)
            ).rowcount
        elapsed = time.monotonic() - started
        status = "completed_with_errors" if errors else "completed"
        with catalog.transaction() as connection:
            connection.execute(
                """UPDATE scans SET finished_at=?,status=?,file_count=?,byte_count=?,hashed_bytes=?,
                elapsed_seconds=?,error=? WHERE id=?""",
                (utc_now(), status, total_files, total_bytes, hashed_bytes, elapsed,
                 "\n".join(errors[:100]) or None, scan_id),
            )
        if elapsed > 0:
            catalog.record_rate("incremental_files", total_files / elapsed)
            if hashed_bytes:
                catalog.record_rate("full_hash_bytes", hashed_bytes / elapsed)
        return ScanResult(scan_id, total_files, total_bytes, hashed_files, hashed_bytes, removed, errors, elapsed)
    except BaseException as exc:
        flush()
        with catalog.transaction() as connection:
            connection.execute(
                "UPDATE scans SET finished_at=?,status='failed',error=? WHERE id=?",
                (utc_now(), str(exc), scan_id),
            )
        raise
