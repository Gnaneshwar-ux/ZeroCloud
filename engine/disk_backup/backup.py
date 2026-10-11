from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .database import Catalog, utc_now
from .devices import Device, iter_files
from .hashing import copy_and_hash, sha256_file, publish_without_overwrite
from .media import classify_name
from .mtp import MtpEntry, collect_entries, copy_entry_to
from .selection import SourceFile, collect_filesystem_sources


@dataclass(slots=True)
class BackupEstimate:
    files: int
    bytes: int
    seconds: float


@dataclass(slots=True)
class BackupResult:
    job_id: str
    copied: int
    skipped: int
    failed: int
    bytes_processed: int
    elapsed_seconds: float
    status: str


def _safe_component(value: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if char in forbidden else char for char in value).strip(" .")
    return cleaned or "source"


def collect_sources(paths: Iterable[Path]) -> list[tuple[Path, Path]]:
    """Return (absolute file, logical relative path) without following symlinks."""
    result: list[tuple[Path, Path]] = []
    for source in paths:
        source = source.resolve()
        if source.is_file():
            result.append((source, Path(source.name)))
        elif source.is_dir():
            prefix = Path(_safe_component(source.name or source.anchor.rstrip("\\/")))
            result.extend((path, prefix / path.relative_to(source)) for path in iter_files(source))
        else:
            raise FileNotFoundError(source)
    return result


def estimate_backup(catalog: Catalog, paths: Iterable[Path]) -> BackupEstimate:
    entries = collect_sources(paths)
    byte_count = sum(path.stat().st_size for path, _ in entries)
    rate = catalog.estimate_rate("backup_bytes", 60 * 1024 * 1024)
    return BackupEstimate(len(entries), byte_count, byte_count / rate if byte_count else 0)


def estimate_selected(
    catalog: Catalog,
    entries: Iterable[SourceFile],
    source_kind: str = "filesystem",
) -> BackupEstimate:
    selected = list(entries)
    byte_count = sum(entry.size for entry in selected)
    if source_kind == "mtp":
        byte_rate = catalog.estimate_rate("mtp_backup_bytes", 6 * 1024 * 1024)
        file_rate = catalog.estimate_rate("mtp_backup_files", 0.8)
        seconds = max(
            byte_count / byte_rate if byte_count else 0,
            len(selected) / file_rate if selected else 0,
        )
        return BackupEstimate(len(selected), byte_count, seconds)
    rate = catalog.estimate_rate("backup_bytes", 60 * 1024 * 1024)
    return BackupEstimate(len(selected), byte_count, byte_count / rate if byte_count else 0)


def validate_target_location(source_paths: Iterable[Path], target_root: Path) -> None:
    target = target_root.resolve()
    for source_path in source_paths:
        source = source_path.resolve()
        if not source.is_dir():
            continue
        try:
            target.relative_to(source)
        except ValueError:
            continue
        raise ValueError(f"Backup target {target} cannot be inside source directory {source}.")


class BackupManager:
    def __init__(self, catalog: Catalog, staging_root: Path):
        self.catalog = catalog
        self.staging_root = staging_root
        self.staging_root.mkdir(parents=True, exist_ok=True)
        # Newly planned MTP jobs can reuse their live COM entries. Resumed jobs
        # safely re-resolve them because COM objects cannot survive app restarts.
        self._live_mtp_entries: dict[str, dict[str, MtpEntry]] = {}

    def create_job(
        self,
        source_paths: list[Path],
        target_device: Device,
        target_root: Path,
        included_entries: list[SourceFile] | None = None,
        selection: dict[str, object] | None = None,
    ) -> str:
        if not target_device.is_filesystem:
            raise ValueError("The backup target must be a filesystem disk.")
        validate_target_location(source_paths, target_root)
        selected = included_entries if included_entries is not None else collect_filesystem_sources(source_paths)
        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        now = utc_now()
        total_bytes = sum(entry.size for entry in selected)
        self.catalog.upsert_volume(target_device.id, target_device.label, target_device.root, target_device.kind)
        with self.catalog.transaction() as connection:
            connection.execute(
                """INSERT INTO backup_jobs(id,source_kind,source_ref,target_volume_id,target_root,status,
                created_at,updated_at,total_files,total_bytes,selection_json) VALUES(?,?,?,?,?,'planned',?,?,?,?,?)""",
                (job_id, "filesystem", json.dumps([str(path) for path in source_paths]),
                 target_device.id, str(target_root.resolve()), now, now, len(selected), total_bytes,
                 json.dumps(selection or {})),
            )
            connection.executemany(
                """INSERT INTO backup_items(job_id,source_path,relative_path,size,mtime_ns,status,
                source_name,extension,category) VALUES(?,?,?,?,?,'pending',?,?,?)""",
                [
                    (job_id, entry.source_path, entry.relative_path, entry.size, entry.mtime_ns,
                     entry.name, entry.extension, entry.category)
                    for entry in selected
                ],
            )
        return job_id

    def create_mtp_job(
        self,
        device_id: str,
        selected_paths: list[str],
        entries: list[MtpEntry],
        target_device: Device,
        target_root: Path,
        selection: dict[str, object] | None = None,
        included_files: list[SourceFile] | None = None,
    ) -> str:
        if not target_device.is_filesystem:
            raise ValueError("The backup target must be a filesystem disk.")
        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        now = utc_now()
        self.catalog.upsert_volume(target_device.id, target_device.label, target_device.root, target_device.kind)
        source_ref = json.dumps({"device_id": device_id, "paths": selected_paths})
        planned_files = included_files if included_files is not None else [
            SourceFile(entry.logical_path, entry.logical_path, entry.name, entry.size, entry.mtime_ns,
                       entry.extension, entry.category, "", str(Path(entry.logical_path).parent))
            for entry in entries
        ]
        with self.catalog.transaction() as connection:
            connection.execute(
                """INSERT INTO backup_jobs(id,source_kind,source_ref,target_volume_id,target_root,status,
                created_at,updated_at,total_files,total_bytes,selection_json) VALUES(?,?,?,?,?,'planned',?,?,?,?,?)""",
                (job_id, "mtp", source_ref, target_device.id, str(target_root.resolve()), now, now,
                 len(planned_files), sum(entry.size for entry in planned_files), json.dumps(selection or {})),
            )
            connection.executemany(
                """INSERT INTO backup_items(job_id,source_path,relative_path,size,mtime_ns,status,
                source_name,extension,category) VALUES(?,?,?,?,?,'pending',?,?,?)""",
                [(job_id, entry.source_path, entry.relative_path, entry.size, entry.mtime_ns,
                  entry.name, entry.extension, entry.category) for entry in planned_files],
            )
        self._live_mtp_entries[job_id] = {entry.logical_path: entry for entry in entries}
        return job_id

    @contextmanager
    def _job_process_lock(self, job_id: str):
        lock_path = self.staging_root / f"{job_id}.run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise RuntimeError(
                        f"Backup {job_id} is already running in another app window."
                    ) from exc
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            handle.close()

    def run_job(
        self,
        job_id: str,
        progress: Callable[[int, int, int, str], None] | None = None,
    ) -> BackupResult:
        with self._job_process_lock(job_id):
            return self._run_job(job_id, progress)

    def _run_job(
        self,
        job_id: str,
        progress: Callable[[int, int, int, str], None] | None = None,
    ) -> BackupResult:
        job = self.catalog.connection.execute("SELECT * FROM backup_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise KeyError(f"Unknown backup job: {job_id}")
        if job["source_kind"] == "mtp":
            return self.run_mtp_job(job_id, progress)
        target_root = Path(job["target_root"])
        target_root.mkdir(parents=True, exist_ok=True)
        target_volume = str(job["target_volume_id"])
        source_roots = tuple(Path(value).resolve() for value in json.loads(job['source_ref']))
        started = time.monotonic()
        copied = skipped = failed = 0
        initial_processed = int(self.catalog.connection.execute(
            "SELECT COALESCE(SUM(size),0) FROM backup_items WHERE job_id=? AND status IN ('copied','skipped_duplicate')",
            (job_id,),
        ).fetchone()[0])
        processed_bytes = initial_processed
        with self.catalog.transaction() as connection:
            connection.execute(
                "UPDATE backup_jobs SET status='running',updated_at=? WHERE id=?", (utc_now(), job_id)
            )
        items = self.catalog.connection.execute(
            "SELECT * FROM backup_items WHERE job_id=? AND status IN ('pending','failed') ORDER BY source_path",
            (job_id,),
        ).fetchall()
        try:
            for item in items:
                source = Path(item["source_path"])
                size = int(item["size"])
                try:
                    stat = source.stat()
                    if stat.st_size != size or stat.st_mtime_ns != int(item["mtime_ns"]):
                        raise RuntimeError("Source changed after the job was planned; create a new job for this file.")
                    target = self._available_target(target_root / Path(item["relative_path"]))
                    if target_root.resolve() not in target.resolve().parents:
                        raise ValueError('A routed destination escapes the selected vault; refusing the copy.')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    same_size = self.catalog.connection.execute(
                        "SELECT relative_path,sha256 FROM files WHERE volume_id=? AND size=? AND sha256 IS NOT NULL",
                        (target_volume, size),
                    ).fetchall()
                    digest: str
                    duplicate = None
                    if same_size:
                        digest = sha256_file(source)
                        duplicate = self._find_verified_duplicate(target_volume, size, digest, source_roots, source)
                    if duplicate:
                        self._update_item(job_id, str(source), "skipped_duplicate", digest, None, str(duplicate), None)
                        skipped += 1
                    else:
                        temporary = target.with_name(f".{target.name}.{job_id}.partial")
                        if temporary.exists():
                            temporary.unlink()
                        copied_digest = copy_and_hash(source, temporary)
                        if same_size and copied_digest != digest:
                            temporary.unlink(missing_ok=True)
                            raise IOError("Hash changed while copying; source may have been modified.")
                        digest = copied_digest
                        after = source.stat()
                        if after.st_size != size or after.st_mtime_ns != int(item['mtime_ns']):
                            raise IOError('Source changed while copying; it was kept and this item needs a new plan.')
                        publish_without_overwrite(temporary, target)
                        if sha256_file(target) != digest:
                            raise IOError('Destination failed SHA-256 read-back verification.')
                        shutil.copystat(source, target, follow_symlinks=False)
                        volume = self.catalog.connection.execute('SELECT root FROM volumes WHERE id=?',(target_volume,)).fetchone()
                        relative_to_volume = target.relative_to(Path(volume['root'])).as_posix()
                        self._catalog_target(target_volume, relative_to_volume, target, digest, None)
                        self._update_item(job_id, str(source), "copied", digest, str(target), None, None)
                        copied += 1
                    processed_bytes += size
                except Exception as exc:
                    self._update_item(job_id, str(source), "failed", None, None, None, str(exc))
                    failed += 1
                completed = copied + skipped + failed
                self._update_job_counts(job_id, processed_bytes)
                if progress:
                    progress(completed, len(items), processed_bytes, source.name)
            status = "completed_with_errors" if failed else "completed"
        except KeyboardInterrupt:
            status = "paused"
        elapsed = time.monotonic() - started
        with self.catalog.transaction() as connection:
            connection.execute(
                "UPDATE backup_jobs SET status=?,updated_at=? WHERE id=?", (status, utc_now(), job_id)
            )
        new_bytes = processed_bytes - initial_processed
        if elapsed > 0 and new_bytes:
            self.catalog.record_rate("backup_bytes", new_bytes / elapsed)
        totals = self.catalog.connection.execute(
            """SELECT SUM(status='copied'),SUM(status='skipped_duplicate'),SUM(status='failed')
            FROM backup_items WHERE job_id=?""", (job_id,)
        ).fetchone()
        return BackupResult(job_id, *(int(value or 0) for value in totals), processed_bytes, elapsed, status)

    def run_mtp_job(
        self,
        job_id: str,
        progress: Callable[[int, int, int, str], None] | None = None,
    ) -> BackupResult:
        job = self.catalog.connection.execute("SELECT * FROM backup_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["source_kind"] != "mtp":
            raise KeyError(f"Unknown MTP backup job: {job_id}")
        source_ref = json.loads(job["source_ref"])
        live_entries = self._live_mtp_entries.pop(job_id, None)
        if live_entries is None:
            live_entries = {
                entry.logical_path: entry
                for entry in collect_entries(source_ref["device_id"], source_ref["paths"])
            }
        items = self.catalog.connection.execute(
            "SELECT * FROM backup_items WHERE job_id=? AND status IN ('pending','failed') ORDER BY source_path", (job_id,)
        ).fetchall()
        target_root = Path(job["target_root"])
        target_root.mkdir(parents=True, exist_ok=True)
        staging = self.staging_root / job_id
        staging.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        copied = skipped = failed = 0
        initial_processed = int(self.catalog.connection.execute(
            "SELECT COALESCE(SUM(size),0) FROM backup_items WHERE job_id=? AND status IN ('copied','skipped_duplicate')",
            (job_id,),
        ).fetchone()[0])
        processed_bytes = initial_processed
        with self.catalog.transaction() as connection:
            connection.execute("UPDATE backup_jobs SET status='running',updated_at=? WHERE id=?", (utc_now(), job_id))
        try:
            for item in items:
                logical = str(item["source_path"])
                try:
                    entry = live_entries.get(logical)
                    if not entry:
                        raise FileNotFoundError("The source item is no longer present on the portable device.")
                    if entry.size != int(item["size"]):
                        raise RuntimeError("The source item changed after the job was planned.")
                    staged = copy_entry_to(entry, staging)
                    digest = sha256_file(staged)
                    actual_size = staged.stat().st_size
                    duplicate = self._find_verified_duplicate(str(job["target_volume_id"]), actual_size, digest)
                    if duplicate:
                        staged.unlink(missing_ok=True)
                        self._update_item(job_id, logical, "skipped_duplicate", digest, None, str(duplicate), None)
                        skipped += 1
                    else:
                        target = self._available_target(target_root / Path(item["relative_path"]))
                        if target_root.resolve() not in target.resolve().parents:
                            raise ValueError('A routed destination escapes the selected vault; refusing the copy.')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        publish_without_overwrite(staged, target)
                        if sha256_file(target) != digest:
                            raise IOError('Destination failed SHA-256 read-back verification.')
                        volume = self.catalog.connection.execute('SELECT root FROM volumes WHERE id=?',(job['target_volume_id'],)).fetchone()
                        relative_to_volume = target.relative_to(Path(volume['root'])).as_posix()
                        self._catalog_target(str(job["target_volume_id"]), relative_to_volume, target, digest, None)
                        self._update_item(job_id, logical, "copied", digest, str(target), None, None)
                        copied += 1
                    processed_bytes += entry.size
                except Exception as exc:
                    self._update_item(job_id, logical, "failed", None, None, None, str(exc))
                    failed += 1
                completed = copied + skipped + failed
                self._update_job_counts(job_id, processed_bytes)
                if progress:
                    progress(completed, len(items), processed_bytes, Path(logical).name)
            status = "completed_with_errors" if failed else "completed"
        except KeyboardInterrupt:
            status = "paused"
        elapsed = time.monotonic() - started
        with self.catalog.transaction() as connection:
            connection.execute("UPDATE backup_jobs SET status=?,updated_at=? WHERE id=?", (status, utc_now(), job_id))
        new_bytes = processed_bytes - initial_processed
        if elapsed > 0 and new_bytes:
            self.catalog.record_rate("mtp_backup_bytes", new_bytes / elapsed)
            self.catalog.record_rate("mtp_backup_files", (copied + skipped + failed) / elapsed)
        totals = self.catalog.connection.execute(
            "SELECT SUM(status='copied'),SUM(status='skipped_duplicate'),SUM(status='failed') FROM backup_items WHERE job_id=?",
            (job_id,),
        ).fetchone()
        return BackupResult(job_id, *(int(value or 0) for value in totals), processed_bytes, elapsed, status)

    def cleanup_sources(self, job_id: str, *, include_duplicates: bool = False) -> tuple[int, list[str]]:
        """Delete only source files whose target content is still present and hash-verified."""
        job = self.catalog.connection.execute("SELECT source_kind FROM backup_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise KeyError(job_id)
        if job["source_kind"] == "mtp":
            raise RuntimeError("Phone cleanup is intentionally disabled; review and delete on the phone manually.")
        statuses = ["copied"] + (["skipped_duplicate"] if include_duplicates else [])
        placeholders = ",".join("?" for _ in statuses)
        items = self.catalog.connection.execute(
            f"SELECT * FROM backup_items WHERE job_id=? AND status IN ({placeholders}) AND cleanup_status='not_requested'",
            (job_id, *statuses),
        ).fetchall()
        deleted = 0
        errors: list[str] = []
        for item in items:
            source = Path(item["source_path"])
            proof = item["target_path"] or item["duplicate_of"]
            try:
                if not proof or not Path(proof).is_file():
                    raise FileNotFoundError("Verified target copy is no longer available.")
                if sha256_file(Path(proof)) != item["sha256"]:
                    raise IOError("Target hash no longer matches the recorded backup hash.")
                if source.is_file():
                    if sha256_file(source) != item["sha256"]:
                        raise IOError("Source changed since backup; refusing cleanup.")
                    source.unlink()
                with self.catalog.transaction() as connection:
                    connection.execute(
                        "UPDATE backup_items SET cleanup_status='deleted' WHERE job_id=? AND source_path=?",
                        (job_id, str(source)),
                    )
                deleted += 1
            except Exception as exc:
                errors.append(f"{source}: {exc}")
        return deleted, errors

    def _catalog_target(self, volume_id: str, relative: str, target: Path, digest: str, scan_id: int | None) -> None:
        # Backup-created files are catalogued immediately. A synthetic scan keeps FK integrity.
        with self.catalog.transaction() as connection:
            if scan_id is None:
                cursor = connection.execute(
                    """INSERT INTO scans(volume_id,mode,started_at,finished_at,status,file_count,
                    byte_count,hashed_bytes,elapsed_seconds,origin)
                    VALUES(?, 'incremental', ?, ?, 'completed', 1, ?, ?, 0, 'backup')""",
                    (volume_id, utc_now(), utc_now(), target.stat().st_size, target.stat().st_size),
                )
                scan_id = int(cursor.lastrowid)
            stat = target.stat()
            connection.execute(
                """INSERT INTO files(volume_id,relative_path,name,size,mtime_ns,sha256,last_seen_scan,last_verified_at,extension,category)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(volume_id,relative_path) DO UPDATE SET
                name=excluded.name,size=excluded.size,mtime_ns=excluded.mtime_ns,sha256=excluded.sha256,
                last_seen_scan=excluded.last_seen_scan,last_verified_at=excluded.last_verified_at,
                extension=excluded.extension,category=excluded.category""",
                (volume_id, relative, target.name, stat.st_size, stat.st_mtime_ns, digest, scan_id,
                 utc_now(), *classify_name(target.name)),
            )

    def _update_item(self, job_id: str, source: str, status: str, digest: str | None,
                     target: str | None, duplicate: str | None, error: str | None) -> None:
        with self.catalog.transaction() as connection:
            connection.execute(
                """UPDATE backup_items SET status=?,sha256=COALESCE(?,sha256),target_path=COALESCE(?,target_path),
                duplicate_of=COALESCE(?,duplicate_of),error=? WHERE job_id=? AND source_path=?""",
                (status, digest, target, duplicate, error, job_id, source),
            )

    def _update_job_counts(self, job_id: str, completed_bytes: int) -> None:
        totals = self.catalog.connection.execute(
            """SELECT SUM(status IN ('copied','skipped_duplicate','failed')),
            SUM(status='copied'),SUM(status='skipped_duplicate'),SUM(status='failed'),
            COALESCE(SUM(CASE WHEN status IN ('copied','skipped_duplicate') THEN size ELSE 0 END),0)
            FROM backup_items WHERE job_id=?""", (job_id,)
        ).fetchone()
        with self.catalog.transaction() as connection:
            connection.execute(
                """UPDATE backup_jobs SET completed_files=?,completed_bytes=?,copied_files=?,
                skipped_files=?,failed_files=?,updated_at=? WHERE id=?""",
                (int(totals[0] or 0), int(totals[4] or completed_bytes), int(totals[1] or 0),
                 int(totals[2] or 0), int(totals[3] or 0), utc_now(), job_id),
            )

    @staticmethod
    def _available_target(path: Path) -> Path:
        if not path.exists():
            return path
        for number in range(1, 100_000):
            candidate = path.with_name(f"{path.stem} (backup {number}){path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not select a non-conflicting target for {path}")

    def _find_verified_duplicate(self, volume_id: str, size: int, digest: str,
                                 source_roots: tuple[Path,...] = (), source: Path | None = None) -> Path | None:
        volume = self.catalog.connection.execute("SELECT root FROM volumes WHERE id=?", (volume_id,)).fetchone()
        if not volume:
            return None
        candidates = self.catalog.connection.execute(
            "SELECT relative_path FROM files WHERE volume_id=? AND size=? AND sha256=?",
            (volume_id, size, digest),
        ).fetchall()
        for candidate in candidates:
            path = Path(volume["root"]) / Path(str(candidate["relative_path"]))
            try:
                resolved = path.resolve()
                if any(resolved == root or root in resolved.parents for root in source_roots):
                    continue
                if source and path.exists() and os.path.samefile(path,source):
                    continue
                if path.is_file() and path.stat().st_size == size and sha256_file(path) == digest:
                    return path
            except (OSError, PermissionError):
                continue
        return None
