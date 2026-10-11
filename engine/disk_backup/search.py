from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from .database import Catalog


@dataclass(frozen=True, slots=True)
class SearchResult:
    score: float
    name: str
    category: str
    size: int
    location: str
    original_source: str
    source_type: str
    recorded_at: str
    modified_at: str
    sha256: str | None


@dataclass(frozen=True, slots=True)
class IndexHealth:
    volume_id: str
    label: str
    root: str
    indexed_at: str | None
    latest_status: str | None
    age_days: float | None

    @property
    def recommendation(self) -> str:
        if not self.indexed_at:
            return "Complete scan recommended"
        if self.latest_status not in {None, "completed"}:
            return "Previous scan incomplete — update recommended"
        if self.age_days is not None and self.age_days > 7:
            return "Older than 7 days — update recommended"
        return "Recent; update if files changed since this time"


def search_catalog(
    catalog: Catalog,
    query: str = "",
    limit: int = 50,
    *,
    categories: set[str] | None = None,
    location: str = "",
    modified_from: date | None = None,
    modified_to: date | None = None,
) -> list[SearchResult]:
    query = query.strip()
    location_filter = location.strip()
    if not query and not any((categories, location_filter, modified_from, modified_to)):
        return []
    indexed_sql = """SELECT f.name,f.category,f.size,f.mtime_ns,f.sha256,
        v.root,v.label,v.id volume_id,f.relative_path,
        COALESCE((SELECT MAX(s.finished_at) FROM scans s
            WHERE s.volume_id=v.id AND s.status='completed'),v.last_seen_at) recorded_at
        FROM files f JOIN volumes v ON v.id=f.volume_id"""
    backup_sql = """SELECT COALESCE(i.source_name,i.source_path) name,i.category,i.size,
        i.mtime_ns,i.sha256,COALESCE(i.target_path,i.duplicate_of) location,
        i.source_path original_source,j.created_at recorded_at
        FROM backup_items i JOIN backup_jobs j ON j.id=i.job_id
        WHERE i.status IN ('copied','skipped_duplicate')
        AND COALESCE(i.target_path,i.duplicate_of) IS NOT NULL"""

    if query:
        direct_pattern = f"%{_escape_like(query.lower())}%"
        indexed = catalog.connection.execute(
            indexed_sql + " WHERE (lower(f.name) LIKE ? ESCAPE '\\' "
            "OR lower(f.relative_path) LIKE ? ESCAPE '\\') LIMIT 1000",
            (direct_pattern, direct_pattern),
        ).fetchall()
        backups = catalog.connection.execute(
            backup_sql + " AND (lower(COALESCE(i.source_name,i.source_path)) LIKE ? ESCAPE '\\' "
            "OR lower(i.source_path) LIKE ? ESCAPE '\\' "
            "OR lower(COALESCE(i.target_path,i.duplicate_of)) LIKE ? ESCAPE '\\') LIMIT 1000",
            (direct_pattern, direct_pattern, direct_pattern),
        ).fetchall()
    else:
        indexed = catalog.connection.execute(indexed_sql + " LIMIT 50000").fetchall()
        backups = catalog.connection.execute(backup_sql + " LIMIT 20000").fetchall()

    # If the exact substring search is sparse, use a bounded name sample for typo-tolerant matches.
    if query and len(indexed) + len(backups) < limit:
        indexed = catalog.connection.execute(indexed_sql + " LIMIT 50000").fetchall()
        backups = catalog.connection.execute(backup_sql + " LIMIT 20000").fetchall()

    results: dict[str, SearchResult] = {}
    for row in indexed:
        location = str(Path(str(row["root"])) / Path(str(row["relative_path"])))
        result = SearchResult(
            _combined_score(query, str(row["name"]), location, location),
            str(row["name"]), str(row["category"]),
            int(row["size"]), location, location, f"Indexed: {row['label']}",
            str(row["recorded_at"] or ""), _modified_at(int(row["mtime_ns"])),
            str(row["sha256"]) if row["sha256"] else None,
        )
        if _matches_filters(result, categories, location_filter=location_filter,
                            modified_from=modified_from, modified_to=modified_to):
            _keep_best(results, result)
    for row in backups:
        stored_location = str(row["location"])
        original_source = str(row["original_source"])
        result = SearchResult(
            _combined_score(query, str(row["name"]), stored_location, original_source),
            str(row["name"]), str(row["category"]), int(row["size"]), stored_location,
            original_source, "Backup history", str(row["recorded_at"] or ""),
            _modified_at(int(row["mtime_ns"])),
            str(row["sha256"]) if row["sha256"] else None,
        )
        if _matches_filters(result, categories, location_filter=location_filter,
                            modified_from=modified_from, modified_to=modified_to):
            _keep_best(results, result)
    threshold = 0.0 if not query else (0.30 if len(_normalize(query)) >= 4 else 0.45)
    return sorted(
        (result for result in results.values() if result.score >= threshold),
        key=lambda result: (-result.score, result.name.lower(), result.location.lower()),
    )[:limit]


BLOCKED_OPEN_EXTENSIONS = frozenset({
    ".bat", ".cmd", ".com", ".exe", ".js", ".jse", ".lnk", ".msi",
    ".ps1", ".reg", ".scr", ".vbs", ".vbe", ".wsf",
})


def open_stored_file(location: str) -> Path:
    """Open a cataloged file with its default app without executing risky file types."""
    path = Path(location)
    if not path.is_file():
        raise FileNotFoundError(f"Stored file is not currently available: {location}")
    if path.suffix.lower() in BLOCKED_OPEN_EXTENSIONS:
        raise PermissionError(
            f"Direct opening is blocked for {path.suffix or 'this'} files; open its folder instead."
        )
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return path


def reveal_stored_file(location: str) -> Path:
    """Show a cataloged file in its containing folder without opening the file itself."""
    path = Path(location)
    if not path.exists():
        raise FileNotFoundError(f"Stored location is not currently available: {location}")
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])
    return path


def open_search_results(results: list[SearchResult]) -> tuple[int, list[str]]:
    """Open distinct safe files and return the number opened plus readable errors."""
    opened = 0
    errors: list[str] = []
    seen: set[str] = set()
    for result in results:
        key = result.location.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            open_stored_file(result.location)
            opened += 1
        except (FileNotFoundError, OSError, PermissionError) as exc:
            errors.append(f"{result.name}: {exc}")
    return opened, errors


def index_health(catalog: Catalog) -> list[IndexHealth]:
    rows = catalog.connection.execute(
        """SELECT v.id,v.label,v.root,
        (SELECT MAX(s.finished_at) FROM scans s WHERE s.volume_id=v.id
            AND s.status IN ('completed','completed_with_errors')
            AND s.origin='index') indexed_at,
        (SELECT s.status FROM scans s WHERE s.volume_id=v.id
            AND s.origin='index' ORDER BY s.id DESC LIMIT 1) latest_status
        FROM volumes v WHERE v.kind IN ('fixed','removable','network') ORDER BY v.label"""
    ).fetchall()
    now = datetime.now(timezone.utc)
    result: list[IndexHealth] = []
    for row in rows:
        indexed_at = str(row["indexed_at"]) if row["indexed_at"] else None
        age_days = None
        if indexed_at:
            try:
                stamp = datetime.fromisoformat(indexed_at)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (now - stamp).total_seconds() / 86400)
            except ValueError:
                pass
        result.append(IndexHealth(
            str(row["id"]), str(row["label"]), str(row["root"]), indexed_at,
            str(row["latest_status"]) if row["latest_status"] else None, age_days,
        ))
    return result


def _score(query: str, name: str) -> float:
    query_normal = _normalize(query)
    name_normal = _normalize(name)
    if not query_normal or not name_normal:
        return 0.0
    if query_normal == name_normal:
        return 1.0
    if name_normal.startswith(query_normal):
        return 0.98
    if query_normal in name_normal:
        return 0.95
    query_tokens = set(query_normal.split())
    name_tokens = set(name_normal.split())
    token_overlap = len(query_tokens & name_tokens) / max(1, len(query_tokens))
    sequence = SequenceMatcher(None, query_normal, name_normal).ratio()
    stem_sequence = SequenceMatcher(
        None, Path(query_normal).stem, Path(name_normal).stem,
    ).ratio()
    return max(sequence, stem_sequence, token_overlap * 0.9)


def _combined_score(query: str, name: str, location: str, original_source: str) -> float:
    if not query:
        return 1.0
    return max(
        _score(query, name),
        _score(query, location) * 0.96,
        _score(query, original_source) * 0.94,
    )


def _modified_at(mtime_ns: int) -> str:
    try:
        return datetime.fromtimestamp(mtime_ns / 1_000_000_000).astimezone().isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _matches_filters(
    result: SearchResult,
    categories: set[str] | None,
    *,
    location_filter: str,
    modified_from: date | None,
    modified_to: date | None,
) -> bool:
    if categories and result.category not in categories:
        return False
    if location_filter:
        wanted = _normalize(location_filter)
        searchable = _normalize(f"{result.location} {result.original_source}")
        if wanted not in searchable:
            return False
    if modified_from or modified_to:
        if not result.modified_at:
            return False
        try:
            modified = datetime.fromisoformat(result.modified_at).date()
        except ValueError:
            return False
        if modified_from and modified < modified_from:
            return False
        if modified_to and modified > modified_to:
            return False
    return True


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _keep_best(results: dict[str, SearchResult], candidate: SearchResult) -> None:
    key = candidate.location.lower()
    current = results.get(key)
    if current is None or candidate.score > current.score or (
        candidate.score == current.score and candidate.source_type == 'Backup history'
        and current.source_type != 'Backup history'
    ):
        results[key] = candidate
