from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .database import Catalog, utc_now
from .hashing import sha256_file
from .mtp import collect_entries, delete_entry, delete_resolved_entries, resolve_delete_entries
from .selection import SourceFile


@dataclass(frozen=True, slots=True)
class CleanupSession:
    id: str
    source_device_id: str
    source_kind: str
    queue_type: str
    status: str
    threshold_bytes: int
    limit_count: int


class CleanupManager:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def resumable_sessions(self) -> list[object]:
        return self.catalog.connection.execute(
            """SELECT s.*,
            SUM(i.status='pending') pending_count,
            SUM(i.status='deleted') deleted_count,
            COALESCE(SUM(i.freed_bytes),0) freed_bytes
            FROM cleanup_sessions s LEFT JOIN cleanup_items i ON i.session_id=s.id
            WHERE s.status IN ('planned','running','paused')
            GROUP BY s.id ORDER BY s.updated_at DESC"""
        ).fetchall()

    def create_largest(self, volume_id: str, threshold_bytes: int = 100 * 1024**2,
                       limit_count: int = 200) -> str:
        volume = self.catalog.connection.execute("SELECT * FROM volumes WHERE id=?", (volume_id,)).fetchone()
        if not volume:
            raise KeyError(volume_id)
        rows = self.catalog.connection.execute(
            """SELECT relative_path,name,size,mtime_ns,extension,category,sha256
            FROM files WHERE volume_id=? AND size>=? ORDER BY size DESC LIMIT ?""",
            (volume_id, threshold_bytes, limit_count),
        ).fetchall()
        items = []
        root = Path(volume["root"])
        for rank, row in enumerate(rows, 1):
            source = root / Path(str(row["relative_path"]))
            proof = self._proof_for_source(str(source), row["sha256"])
            items.append((str(source), row["name"], row["size"], row["mtime_ns"], row["extension"],
                          row["category"], row["sha256"], proof, rank))
        return self._create_session(volume_id, str(volume["kind"]), str(volume["root"]),
                                    "largest", threshold_bytes, limit_count, items)

    def create_largest_mtp(
        self,
        device_id: str,
        entries: list[SourceFile],
        threshold_bytes: int = 100 * 1024**2,
        limit_count: int = 200,
    ) -> str:
        """Create a review queue from a read-only live portable-device inventory."""
        candidates = sorted(
            (entry for entry in entries if entry.size >= threshold_bytes),
            key=lambda entry: (-entry.size, entry.source_path.lower()),
        )[:limit_count]
        items = [
            (
                entry.source_path, entry.name, entry.size, entry.mtime_ns,
                entry.extension, entry.category, None, None, rank,
            )
            for rank, entry in enumerate(candidates, 1)
        ]
        return self._create_session(
            device_id, "mtp", device_id, "largest", threshold_bytes, limit_count, items,
            source_device_id=device_id,
        )

    def create_duplicates(self, volume_id: str, limit_count: int = 200) -> str:
        volume = self.catalog.connection.execute("SELECT * FROM volumes WHERE id=?", (volume_id,)).fetchone()
        if not volume:
            raise KeyError(volume_id)
        groups = self.catalog.connection.execute(
            """SELECT sha256,size FROM files WHERE volume_id=? AND sha256 IS NOT NULL
            GROUP BY sha256,size HAVING COUNT(*)>1 ORDER BY size*(COUNT(*)-1) DESC""",
            (volume_id,),
        ).fetchall()
        root = Path(volume["root"])
        items = []
        rank = 1
        for group in groups:
            copies = self.catalog.connection.execute(
                """SELECT relative_path,name,size,mtime_ns,extension,category,sha256 FROM files
                WHERE volume_id=? AND sha256=? AND size=?
                ORDER BY mtime_ns ASC,LENGTH(relative_path) ASC,relative_path ASC""",
                (volume_id, group["sha256"], group["size"]),
            ).fetchall()
            keeper = root / Path(str(copies[0]["relative_path"]))
            for copy in copies[1:]:
                source = root / Path(str(copy["relative_path"]))
                items.append((str(source), copy["name"], copy["size"], copy["mtime_ns"],
                              copy["extension"], copy["category"], copy["sha256"], str(keeper), rank))
                rank += 1
                if len(items) >= limit_count:
                    break
            if len(items) >= limit_count:
                break
        return self._create_session(volume_id, str(volume["kind"]), str(volume["root"]),
                                    "duplicates", 0, limit_count, items)

    def create_from_job(self, job_id: str) -> str:
        job = self.catalog.connection.execute("SELECT * FROM backup_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise KeyError(job_id)
        rows = self.catalog.connection.execute(
            """SELECT source_path,COALESCE(source_name,source_path) source_name,size,mtime_ns,
            extension,category,sha256,COALESCE(target_path,duplicate_of) proof_path
            FROM backup_items WHERE job_id=? AND status IN ('copied','skipped_duplicate')
            AND cleanup_status='not_requested' ORDER BY size DESC""",
            (job_id,),
        ).fetchall()
        items = [
            (row["source_path"], row["source_name"], row["size"], row["mtime_ns"], row["extension"],
             row["category"], row["sha256"], row["proof_path"], rank)
            for rank, row in enumerate(rows, 1)
        ]
        return self._create_session(
            job_id, str(job["source_kind"]), f"backup_job:{job_id}",
            "backed_up", 0, len(items), items, source_device_id=self._job_source_device(job),
        )

    def _create_session(self, volume_id: str, source_kind: str, source_ref: str, queue_type: str,
                        threshold: int, limit_count: int, items: list[tuple[object, ...]],
                        source_device_id: str | None = None) -> str:
        session_id = time.strftime("C%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        now = utc_now()
        with self.catalog.transaction() as connection:
            connection.execute(
                """INSERT INTO cleanup_sessions(id,source_device_id,source_kind,source_ref,queue_type,
                status,threshold_bytes,limit_count,created_at,updated_at)
                VALUES(?,?,?,?,?,'planned',?,?,?,?)""",
                (session_id, source_device_id or volume_id, source_kind, source_ref, queue_type,
                 threshold, limit_count, now, now),
            )
            connection.executemany(
                """INSERT INTO cleanup_items(session_id,source_path,source_name,size,mtime_ns,
                extension,category,sha256,proof_path,rank) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [(session_id, *item) for item in items],
            )
        return session_id

    def pending_items(self, session_id: str) -> list[object]:
        return self.catalog.connection.execute(
            "SELECT * FROM cleanup_items WHERE session_id=? AND status='pending' ORDER BY rank",
            (session_id,),
        ).fetchall()

    def session(self, session_id: str) -> object:
        row = self.catalog.connection.execute("SELECT * FROM cleanup_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise KeyError(session_id)
        return row

    def set_session_status(self, session_id: str, status: str) -> None:
        with self.catalog.transaction() as connection:
            connection.execute(
                "UPDATE cleanup_sessions SET status=?,updated_at=? WHERE id=?",
                (status, utc_now(), session_id),
            )

    def cancel_session(self, session_id: str) -> None:
        """Dismiss a review session without changing or deleting any source files."""
        session = self.session(session_id)
        if session["status"] not in {"planned", "running", "paused"}:
            raise RuntimeError("Only a resumable cleanup session can be dismissed.")
        self.set_session_status(session_id, "cancelled")

    def mark_item(self, session_id: str, source_path: str, status: str, action: str,
                  error: str | None = None, freed_bytes: int = 0) -> None:
        with self.catalog.transaction() as connection:
            connection.execute(
                """UPDATE cleanup_items SET status=?,action=?,error=?,freed_bytes=?
                WHERE session_id=? AND source_path=?""",
                (status, action, error, freed_bytes, session_id, source_path),
            )
            connection.execute(
                "UPDATE cleanup_sessions SET updated_at=? WHERE id=?", (utc_now(), session_id)
            )

    def note_item_error(self, session_id: str, source_path: str, error: str) -> None:
        """Record a failed attempt while leaving the item pending and resumable."""
        with self.catalog.transaction() as connection:
            connection.execute(
                """UPDATE cleanup_items SET action='bulk_delete_failed',error=?
                WHERE session_id=? AND source_path=? AND status='pending'""",
                (error, session_id, source_path),
            )
            connection.execute(
                "UPDATE cleanup_sessions SET updated_at=? WHERE id=?", (utc_now(), session_id)
            )

    def attach_latest_proof(self, session_id: str, source_path: str) -> None:
        row = self.catalog.connection.execute(
            """SELECT sha256,COALESCE(target_path,duplicate_of) proof FROM backup_items
            WHERE source_path=? AND status IN ('copied','skipped_duplicate')
            ORDER BY rowid DESC LIMIT 1""", (source_path,)
        ).fetchone()
        if not row or not row["sha256"] or not row["proof"]:
            raise RuntimeError("The backup finished without a usable verification proof.")
        with self.catalog.transaction() as connection:
            connection.execute(
                "UPDATE cleanup_items SET sha256=?,proof_path=? WHERE session_id=? AND source_path=?",
                (row["sha256"], row["proof"], session_id, source_path),
            )

    def ignore_folder(self, session_id: str, source_path: str) -> int:
        parent = str(Path(source_path).parent)
        rows = self.catalog.connection.execute(
            "SELECT source_path FROM cleanup_items WHERE session_id=? AND status='pending'",
            (session_id,),
        ).fetchall()
        matches = [str(row["source_path"]) for row in rows if str(Path(str(row["source_path"])).parent) == parent]
        with self.catalog.transaction() as connection:
            connection.executemany(
                """UPDATE cleanup_items SET status='ignored',action='ignore_folder'
                WHERE session_id=? AND source_path=?""",
                [(session_id, path) for path in matches],
            )
            connection.execute(
                "UPDATE cleanup_sessions SET updated_at=? WHERE id=?", (utc_now(), session_id)
            )
        return len(matches)

    def delete_proven_item(self, session_id: str, source_path: str) -> int:
        session = self.session(session_id)
        item = self.catalog.connection.execute(
            "SELECT * FROM cleanup_items WHERE session_id=? AND source_path=?",
            (session_id, source_path),
        ).fetchone()
        if not item or item["status"] != "pending":
            raise RuntimeError("Cleanup item is not pending.")
        proof = Path(str(item["proof_path"] or ""))
        digest = str(item["sha256"] or "")
        if not digest or not proof.is_file() or sha256_file(proof) != digest:
            raise RuntimeError("Verified backup or duplicate proof is unavailable.")
        if session["source_kind"] == "mtp":
            live = collect_entries(str(session["source_device_id"]), [source_path])
            if len(live) != 1:
                raise RuntimeError("The source item could not be uniquely re-resolved on the device.")
            tolerance = max(1024, int(int(item["size"]) * 0.005))
            if abs(live[0].size - int(item["size"])) > tolerance:
                raise RuntimeError("The source item size changed; deletion refused.")
            if int(item["mtime_ns"]) and live[0].mtime_ns and int(item["mtime_ns"]) != live[0].mtime_ns:
                raise RuntimeError("The source item modification time changed; deletion refused.")
            delete_entry(str(session["source_device_id"]), source_path)
        else:
            source = Path(source_path)
            if not source.is_file() or source.stat().st_size != int(item["size"]):
                raise RuntimeError("Source is missing or changed.")
            if sha256_file(source) != digest:
                raise RuntimeError("Source hash changed; deletion refused.")
            source.unlink()
            self._remove_catalog_entry(source)
        self.mark_item(session_id, source_path, "deleted", "delete", freed_bytes=int(item["size"]))
        with self.catalog.transaction() as connection:
            connection.execute(
                "UPDATE backup_items SET cleanup_status='deleted' "
                "WHERE source_path=? AND status IN ('copied','skipped_duplicate')",
                (source_path,),
            )
        return int(item["size"])

    def delete_proven_mtp_batch(
        self,
        session_id: str,
        source_paths: list[str],
        proof_cache: dict[tuple[str, int, int], str] | None = None,
    ) -> tuple[list[str], dict[str, str], int]:
        """Verify and delete an MTP batch while preserving per-file results."""
        session = self.session(session_id)
        if session["source_kind"] != "mtp":
            raise RuntimeError("MTP batch deletion cannot be used for this source.")
        cache = proof_cache if proof_cache is not None else {}
        placeholders = ",".join("?" for _ in source_paths)
        if not placeholders:
            return [], {}, 0
        rows = self.catalog.connection.execute(
            f"SELECT * FROM cleanup_items WHERE session_id=? AND status='pending' "
            f"AND source_path IN ({placeholders})",
            (session_id, *source_paths),
        ).fetchall()
        by_path = {str(row["source_path"]): row for row in rows}
        errors: dict[str, str] = {}
        verified: list[object] = []
        for source_path in source_paths:
            item = by_path.get(source_path)
            if item is None:
                errors[source_path] = "Cleanup item is not pending."
                continue
            proof = Path(str(item["proof_path"] or ""))
            digest = str(item["sha256"] or "")
            try:
                proof_stat = proof.stat()
                key = (str(proof.resolve()), proof_stat.st_size, proof_stat.st_mtime_ns)
                actual = cache.get(key)
                if actual is None:
                    actual = sha256_file(proof)
                    cache[key] = actual
                if not digest or actual != digest:
                    raise RuntimeError("Verified backup or duplicate proof is unavailable.")
                verified.append(item)
            except Exception as exc:
                errors[source_path] = str(exc)

        resolved, resolve_errors = resolve_delete_entries(
            str(session["source_device_id"]), [str(item["source_path"]) for item in verified],
        )
        errors.update(resolve_errors)
        resolved_by_path = {entry.logical_path: entry for entry in resolved}
        safe_entries = []
        for item in verified:
            source_path = str(item["source_path"])
            live = resolved_by_path.get(source_path)
            if live is None:
                errors.setdefault(source_path, "The source item could not be re-resolved.")
                continue
            tolerance = max(1024, int(int(item["size"]) * 0.005))
            if abs(live.size - int(item["size"])) > tolerance:
                errors[source_path] = "The source item size changed; deletion refused."
                continue
            if int(item["mtime_ns"]) and live.mtime_ns and int(item["mtime_ns"]) != live.mtime_ns:
                errors[source_path] = "The source item modification time changed; deletion refused."
                continue
            safe_entries.append(live)

        deleted, remaining = delete_resolved_entries(
            str(session["source_device_id"]), safe_entries,
        )
        for source_path in remaining:
            errors[source_path] = "Windows did not confirm deletion; the source was kept pending."
        reclaimed = sum(int(by_path[path]["size"]) for path in deleted)
        if deleted:
            now = utc_now()
            with self.catalog.transaction() as connection:
                connection.executemany(
                    """UPDATE cleanup_items SET status='deleted',action='bulk_delete',error=NULL,
                    freed_bytes=? WHERE session_id=? AND source_path=?""",
                    [(int(by_path[path]["size"]), session_id, path) for path in deleted],
                )
                connection.executemany(
                    """UPDATE backup_items SET cleanup_status='deleted'
                    WHERE source_path=? AND status IN ('copied','skipped_duplicate')""",
                    [(path,) for path in deleted],
                )
                connection.execute(
                    "UPDATE cleanup_sessions SET updated_at=? WHERE id=?", (now, session_id),
                )
        return sorted(deleted), errors, reclaimed

    def _remove_catalog_entry(self, source: Path) -> None:
        resolved = source.resolve()
        for volume in self.catalog.connection.execute("SELECT id,root FROM volumes"):
            root = Path(str(volume["root"])).resolve()
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            with self.catalog.transaction() as connection:
                connection.execute(
                    "DELETE FROM files WHERE volume_id=? AND relative_path=?",
                    (volume["id"], relative),
                )
    def _proof_for_source(self, source_path: str, digest: str | None) -> str | None:
        if not digest:
            return None
        row = self.catalog.connection.execute(
            """SELECT COALESCE(target_path,duplicate_of) proof FROM backup_items
            WHERE source_path=? AND sha256=? AND status IN ('copied','skipped_duplicate')
            ORDER BY rowid DESC LIMIT 1""",
            (source_path, digest),
        ).fetchone()
        return str(row["proof"]) if row and row["proof"] else None

    @staticmethod
    def _job_source_device(job: object) -> str:
        if job["source_kind"] != "mtp":
            try:
                paths = json.loads(job["source_ref"])
                return str(paths[0]) if paths else str(job["source_ref"])
            except Exception:
                return str(job["source_ref"])
        try:
            return str(json.loads(job["source_ref"])["device_id"])
        except Exception:
            return str(job["source_ref"])
