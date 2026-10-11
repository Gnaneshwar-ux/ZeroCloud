from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .database import Catalog, utc_now
from .devices import Device
from .hashing import copy_and_hash, sha256_file, publish_without_overwrite
from .mtp import collect_entries, copy_entry_to, copy_file_to_device, delete_entry


RestoreProgress = Callable[[int, int, int, str], None]


@dataclass(frozen=True, slots=True)
class RestoreResult:
    restore_id: str
    restored: int
    skipped: int
    conflicts: int
    failed: int
    completed_bytes: int
    elapsed_seconds: float
    status: str


class RestoreManager:
    def __init__(self, catalog: Catalog, staging_root: Path):
        self.catalog = catalog
        self.staging_root = staging_root / "restore"
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def resumable_jobs(self) -> list[object]:
        return self.catalog.connection.execute(
            """SELECT r.*,
            SUM(i.status IN ('pending','failed')) remaining_files
            FROM restore_jobs r LEFT JOIN restore_items i ON i.restore_id=r.id
            WHERE r.status IN ('planned','running','paused','completed_with_errors')
            GROUP BY r.id HAVING remaining_files>0 ORDER BY r.updated_at DESC"""
        ).fetchall()

    def create_job(
        self,
        backup_job_id: str,
        target_device: Device,
        categories: set[str],
        *,
        deleted_only: bool = True,
    ) -> str:
        if not categories:
            raise ValueError("Choose at least one restore category.")
        backup = self.catalog.connection.execute(
            "SELECT * FROM backup_jobs WHERE id=?", (backup_job_id,)
        ).fetchone()
        if not backup:
            raise KeyError(backup_job_id)
        if backup["source_kind"] == "mtp" and target_device.kind != "mtp":
            raise ValueError("A phone/tablet backup must be restored to a portable device.")
        if backup["source_kind"] != "mtp" and not target_device.is_filesystem:
            raise ValueError("A disk/folder backup must be restored to a filesystem device.")
        placeholders = ",".join("?" for _ in categories)
        cleanup_filter = "AND cleanup_status='deleted'" if deleted_only else ""
        rows = self.catalog.connection.execute(
            f"""SELECT source_path,COALESCE(target_path,duplicate_of) proof_path,
            COALESCE(source_name,source_path) source_name,size,mtime_ns,sha256,extension,category
            FROM backup_items WHERE job_id=? AND status IN ('copied','skipped_duplicate')
            AND sha256 IS NOT NULL AND COALESCE(target_path,duplicate_of) IS NOT NULL
            AND category IN ({placeholders}) {cleanup_filter}
            ORDER BY source_path""",
            (backup_job_id, *sorted(categories)),
        ).fetchall()
        if not rows:
            raise RuntimeError("No backup items match the selected restore scope.")
        restore_id = time.strftime("R%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        now = utc_now()
        target_root = "" if target_device.kind == "mtp" else target_device.root
        selection = {"categories": sorted(categories), "deleted_only": deleted_only}
        items = []
        for row in rows:
            original = str(row["source_path"])
            restore_path = (
                PurePosixPath(original).as_posix()
                if target_device.kind == "mtp"
                else str(self._filesystem_restore_path(Path(target_device.root), Path(original)))
            )
            items.append((
                restore_id, original, str(row["proof_path"]), restore_path,
                str(row["source_name"]), int(row["size"]), int(row["mtime_ns"]),
                str(row["sha256"]), str(row["extension"]), str(row["category"]),
            ))
        with self.catalog.transaction() as connection:
            connection.execute(
                """INSERT INTO restore_jobs(id,backup_job_id,target_device_id,target_kind,target_root,
                status,selection_json,created_at,updated_at,total_files,total_bytes)
                VALUES(?,?,?,?,?,'planned',?,?,?,?,?)""",
                (restore_id, backup_job_id, target_device.id, target_device.kind, target_root,
                 json.dumps(selection), now, now, len(items), sum(item[5] for item in items)),
            )
            connection.executemany(
                """INSERT INTO restore_items(restore_id,original_source_path,proof_path,restore_path,
                source_name,size,mtime_ns,sha256,extension,category)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                items,
            )
        return restore_id

    def run_job(self, restore_id: str, progress: RestoreProgress | None = None) -> RestoreResult:
        job = self.catalog.connection.execute(
            "SELECT * FROM restore_jobs WHERE id=?", (restore_id,)
        ).fetchone()
        if not job:
            raise KeyError(restore_id)
        items = self.catalog.connection.execute(
            """SELECT * FROM restore_items WHERE restore_id=? AND status IN ('pending','failed')
            ORDER BY size DESC,original_source_path""", (restore_id,)
        ).fetchall()
        if not items:
            self._finish_job(restore_id)
            return self.result(restore_id, 0.0)
        with self.catalog.transaction() as connection:
            connection.execute(
                "UPDATE restore_jobs SET status='running',updated_at=? WHERE id=?",
                (utc_now(), restore_id),
            )
        started = time.monotonic()
        initial_completed = int(job["completed_bytes"])
        proof_cache: dict[tuple[str, int, int], str] = {}
        try:
            for position, item in enumerate(items, 1):
                try:
                    proof = Path(str(item["proof_path"]))
                    digest = self._verify_proof(proof, str(item["sha256"]), proof_cache)
                    if job["target_kind"] == "mtp":
                        status = self._restore_mtp(job, item, proof, digest)
                    else:
                        status = self._restore_filesystem(item, proof, digest)
                    self._update_item(restore_id, str(item["original_source_path"]), status, None)
                    if status in {"restored", "skipped_existing"} and self._is_original_destination(job, item):
                        self._mark_source_present(str(job["backup_job_id"]), str(item["original_source_path"]))
                except FileExistsError as exc:
                    self._update_item(restore_id, str(item["original_source_path"]), "conflict", str(exc))
                except Exception as exc:
                    self._update_item(restore_id, str(item["original_source_path"]), "failed", str(exc))
                self._update_counts(restore_id)
                if progress:
                    totals = self.catalog.connection.execute(
                        "SELECT completed_bytes FROM restore_jobs WHERE id=?", (restore_id,)
                    ).fetchone()
                    progress(position, len(items), int(totals[0] or 0), str(item["source_name"]))
            self._finish_job(restore_id)
        except KeyboardInterrupt:
            with self.catalog.transaction() as connection:
                connection.execute(
                    "UPDATE restore_jobs SET status='paused',updated_at=? WHERE id=?",
                    (utc_now(), restore_id),
                )
        elapsed = time.monotonic() - started
        result = self.result(restore_id, elapsed)
        new_bytes = max(0, result.completed_bytes - initial_completed)
        if elapsed > 0 and new_bytes:
            operation = "mtp_restore_bytes" if job["target_kind"] == "mtp" else "restore_bytes"
            self.catalog.record_rate(operation, new_bytes / elapsed)
        return result

    def result(self, restore_id: str, elapsed: float) -> RestoreResult:
        job = self.catalog.connection.execute(
            "SELECT * FROM restore_jobs WHERE id=?", (restore_id,)
        ).fetchone()
        if not job:
            raise KeyError(restore_id)
        return RestoreResult(
            restore_id, int(job["restored_files"]), int(job["skipped_files"]),
            int(job["conflict_files"]), int(job["failed_files"]),
            int(job["completed_bytes"]), elapsed, str(job["status"]),
        )

    def preview(self, restore_id: str, limit: int = 25) -> list[object]:
        return self.catalog.connection.execute(
            """SELECT * FROM restore_items WHERE restore_id=?
            ORDER BY size DESC,original_source_path LIMIT ?""", (restore_id, limit)
        ).fetchall()

    def _restore_filesystem(self, item: object, proof: Path, digest: str) -> str:
        target = Path(str(item["restore_path"]))
        if target.exists():
            if target.is_file() and target.stat().st_size == int(item["size"]) and sha256_file(target) == digest:
                return "skipped_existing"
            raise FileExistsError(f"A different item already exists at {target}; it was not overwritten.")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.restore-partial")
        partial.unlink(missing_ok=True)
        copied_digest = copy_and_hash(proof, partial)
        if copied_digest != digest:
            partial.unlink(missing_ok=True)
            raise IOError("Hash changed while copying the backup proof.")
        publish_without_overwrite(partial, target)
        try:
            os.utime(target, ns=(int(item["mtime_ns"]), int(item["mtime_ns"])))
        except OSError:
            pass
        if sha256_file(target) != digest:
            target.unlink(missing_ok=True)
            raise IOError("Restored file failed final SHA-256 verification.")
        return "restored"

    def _restore_mtp(self, job: object, item: object, proof: Path, digest: str) -> str:
        device_id = str(job["target_device_id"])
        logical = str(item["restore_path"])
        verify_root = self.staging_root / str(job["id"]) / "verify"
        try:
            existing = collect_entries(device_id, [logical])
        except FileNotFoundError:
            existing = []
        if existing:
            if len(existing) != 1 or existing[0].size != int(item["size"]):
                raise FileExistsError(f"A different item already exists at {logical}; it was not overwritten.")
            local_existing = copy_entry_to(existing[0], verify_root)
            try:
                if sha256_file(local_existing) == digest:
                    return "skipped_existing"
            finally:
                local_existing.unlink(missing_ok=True)
            raise FileExistsError(f"A different item already exists at {logical}; it was not overwritten.")

        outbound = proof
        staged_outbound: Path | None = None
        expected_name = PurePosixPath(logical).name
        if proof.name != expected_name:
            outbound_root = self.staging_root / str(job["id"]) / "outbound"
            outbound_root.mkdir(parents=True, exist_ok=True)
            staged_outbound = outbound_root / expected_name
            staged_outbound.unlink(missing_ok=True)
            try:
                os.link(proof, staged_outbound)
            except OSError:
                shutil.copy2(proof, staged_outbound)
            outbound = staged_outbound
        try:
            restored_entry = copy_file_to_device(device_id, outbound, logical)
            verified = copy_entry_to(restored_entry, verify_root)
            try:
                if sha256_file(verified) != digest:
                    try:
                        delete_entry(device_id, logical)
                    except Exception:
                        pass
                    raise IOError("Restored device file failed SHA-256 read-back verification.")
            finally:
                verified.unlink(missing_ok=True)
        finally:
            if staged_outbound is not None:
                staged_outbound.unlink(missing_ok=True)
        return "restored"

    @staticmethod
    def _filesystem_restore_path(target_root: Path, original: Path) -> Path:
        try:
            original.resolve().relative_to(target_root.resolve())
            return original
        except ValueError:
            pass
        try:
            relative = original.relative_to(original.anchor)
        except ValueError:
            relative = original
        return target_root / relative

    @staticmethod
    def _verify_proof(
        proof: Path,
        expected: str,
        cache: dict[tuple[str, int, int], str],
    ) -> str:
        if not proof.is_file():
            raise FileNotFoundError(f"Backup proof is missing: {proof}")
        stat = proof.stat()
        key = (str(proof), stat.st_size, stat.st_mtime_ns)
        digest = cache.get(key)
        if digest is None:
            digest = sha256_file(proof)
            cache[key] = digest
        if digest != expected:
            raise IOError(f"Backup proof hash does not match: {proof}")
        return digest

    def _update_item(self, restore_id: str, original: str, status: str, error: str | None) -> None:
        with self.catalog.transaction() as connection:
            connection.execute(
                """UPDATE restore_items SET status=?,error=?,restored_at=?
                WHERE restore_id=? AND original_source_path=?""",
                (status, error, utc_now() if status in {"restored", "skipped_existing"} else None,
                 restore_id, original),
            )

    def _mark_source_present(self, backup_job_id: str, original: str) -> None:
        with self.catalog.transaction() as connection:
            connection.execute(
                """UPDATE backup_items SET cleanup_status='not_requested'
                WHERE job_id=? AND source_path=?""", (backup_job_id, original),
            )

    def _is_original_destination(self, job: object, item: object) -> bool:
        backup = self.catalog.connection.execute(
            "SELECT source_kind,source_ref FROM backup_jobs WHERE id=?",
            (job["backup_job_id"],),
        ).fetchone()
        if not backup:
            return False
        if backup["source_kind"] == "mtp":
            try:
                original_device = str(json.loads(str(backup["source_ref"]))["device_id"])
            except (ValueError, KeyError, TypeError):
                return False
            return original_device == str(job["target_device_id"])
        try:
            return Path(str(item["restore_path"])).resolve() == Path(
                str(item["original_source_path"])
            ).resolve()
        except OSError:
            return False

    def _update_counts(self, restore_id: str) -> None:
        totals = self.catalog.connection.execute(
            """SELECT SUM(status='restored'),SUM(status='skipped_existing'),
            SUM(status='conflict'),SUM(status='failed'),
            COALESCE(SUM(CASE WHEN status IN ('restored','skipped_existing') THEN size ELSE 0 END),0)
            FROM restore_items WHERE restore_id=?""", (restore_id,)
        ).fetchone()
        with self.catalog.transaction() as connection:
            connection.execute(
                """UPDATE restore_jobs SET restored_files=?,skipped_files=?,conflict_files=?,
                failed_files=?,completed_bytes=?,updated_at=? WHERE id=?""",
                (*(int(value or 0) for value in totals), utc_now(), restore_id),
            )

    def _finish_job(self, restore_id: str) -> None:
        self._update_counts(restore_id)
        row = self.catalog.connection.execute(
            """SELECT SUM(status IN ('pending','failed')),SUM(status='conflict')
            FROM restore_items WHERE restore_id=?""", (restore_id,)
        ).fetchone()
        remaining, conflicts = int(row[0] or 0), int(row[1] or 0)
        status = "completed" if not remaining and not conflicts else "completed_with_errors"
        with self.catalog.transaction() as connection:
            connection.execute(
                "UPDATE restore_jobs SET status=?,updated_at=? WHERE id=?",
                (status, utc_now(), restore_id),
            )
