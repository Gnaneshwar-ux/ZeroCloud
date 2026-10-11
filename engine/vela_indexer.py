"""Offline, idempotent catalog scanner for Vela.

It never changes source media. The SQLite catalog belongs in the app's local data
directory (not inside a watched library) and can safely be scanned repeatedly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

KIND_BY_EXTENSION = {
    **dict.fromkeys({".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".avif", ".tif", ".tiff", ".bmp"}, "image"),
    **dict.fromkeys({".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2"}, "raw"),
    **dict.fromkeys({".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp"}, "video"),
    **dict.fromkeys({".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg"}, "audio"),
    ".pdf": "document",
}
FINGERPRINT_BYTES = 16 * 1024


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS media (
          path TEXT PRIMARY KEY, kind TEXT NOT NULL, extension TEXT NOT NULL,
          byte_size INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
          sample_fingerprint TEXT NOT NULL, indexed_at TEXT NOT NULL,
          missing_since TEXT
        );
        CREATE INDEX IF NOT EXISTS media_kind_index ON media(kind);
        CREATE INDEX IF NOT EXISTS media_missing_index ON media(missing_since);
        CREATE TABLE IF NOT EXISTS cache_entries (
          cache_key TEXT PRIMARY KEY, media_path TEXT NOT NULL REFERENCES media(path),
          derivative_kind TEXT NOT NULL, renderer_version TEXT NOT NULL,
          derivative_path TEXT NOT NULL, byte_size INTEGER NOT NULL DEFAULT 0,
          last_accessed_at TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS cache_lru_index ON cache_entries(last_accessed_at);
        """
    )
    return connection


def sample_fingerprint(path: Path, byte_size: int) -> str:
    """Fast change detector: first and last 16KiB, plus file size.

    A full checksum is intentionally not computed during every refresh. It can be
    scheduled on demand for duplicate detection without delaying the library UI.
    """
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(byte_size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(FINGERPRINT_BYTES))
        if byte_size > FINGERPRINT_BYTES:
            handle.seek(max(FINGERPRINT_BYTES, byte_size - FINGERPRINT_BYTES))
            digest.update(handle.read(FINGERPRINT_BYTES))
    return digest.hexdigest()


def scan(root: Path, database: Path) -> dict[str, object]:
    if not root.is_dir():
        raise ValueError(f"Library folder does not exist: {root}")
    connection = connect(database)
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    created = changed = unchanged = skipped = 0
    now = utc_now()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        extension = path.suffix.lower()
        kind = KIND_BY_EXTENSION.get(extension)
        if kind is None:
            skipped += 1
            continue
        try:
            stat = path.stat()
            canonical_path = str(path.resolve())
            seen.add(canonical_path)
            row = connection.execute("SELECT byte_size, modified_ns, sample_fingerprint FROM media WHERE path = ?", (canonical_path,)).fetchone()
            if row and row["byte_size"] == stat.st_size and row["modified_ns"] == stat.st_mtime_ns:
                connection.execute("UPDATE media SET indexed_at = ?, missing_since = NULL WHERE path = ?", (now, canonical_path))
                unchanged += 1
            else:
                fingerprint = sample_fingerprint(path, stat.st_size)
                connection.execute(
                    """INSERT INTO media(path, kind, extension, byte_size, modified_ns, sample_fingerprint, indexed_at, missing_since)
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, extension=excluded.extension, byte_size=excluded.byte_size,
                    modified_ns=excluded.modified_ns, sample_fingerprint=excluded.sample_fingerprint, indexed_at=excluded.indexed_at, missing_since=NULL""",
                    (canonical_path, kind, extension, stat.st_size, stat.st_mtime_ns, fingerprint, now),
                )
                if row:
                    changed += 1
                else:
                    created += 1
            counts[kind] += 1
        except (OSError, PermissionError):
            skipped += 1
    stored_paths = [row[0] for row in connection.execute("SELECT path FROM media WHERE missing_since IS NULL")]
    missing = [path for path in stored_paths if path not in seen]
    if missing:
        connection.executemany("UPDATE media SET missing_since = ? WHERE path = ?", [(now, path) for path in missing])
    connection.commit()
    return {"root": str(root), "created": created, "changed": changed, "unchanged": unchanged, "missing": len(missing), "skipped": skipped, "by_kind": dict(counts)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Vela's local media catalog.")
    parser.add_argument("root", type=Path, help="Folder to index; source files are never modified")
    parser.add_argument("--database", required=True, type=Path, help="Local Vela SQLite database path")
    arguments = parser.parse_args()
    print(json.dumps(scan(arguments.root, arguments.database), indent=2))


if __name__ == "__main__":
    main()
