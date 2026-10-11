from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .devices import iter_files
from .media import CATEGORY_DESTINATIONS, classify_name, major_folder
from .mtp import MtpEntry


def apply_numbered_selection(
    items: list[tuple[str, bool, int]], answer: str,
) -> list[tuple[str, bool, int]] | None:
    """Apply A/I/S/X syntax to a numbered collection without UI dependencies."""
    tokens = answer.strip().upper().replace(",", " ").split()
    if not tokens or tokens == ["A"]:
        return list(items)
    if tokens == ["X"]:
        return None
    # Plain row numbers are the primary syntax and mean "include only".
    if all(token.isdigit() for token in tokens):
        tokens.insert(0, "I")
    mode = tokens[0]
    if mode not in {"I", "S"}:
        raise ValueError("Enter folder numbers, A for all, or X to cancel.")
    numbers = {
        int(token) for token in tokens[1:]
        if token.isdigit() and 1 <= int(token) <= len(items)
    }
    if not numbers:
        raise ValueError("Enter at least one valid folder number.")
    if mode == "I":
        return [item for number, item in enumerate(items, 1) if number in numbers]
    return [item for number, item in enumerate(items, 1) if number not in numbers]


def apply_category_selection(available: list[str], answer: str) -> set[str] | None:
    """Resolve category shortcuts or displayed row numbers; None means cancel."""
    normalized = answer.strip().upper().replace(" ", "")
    if normalized == "X":
        return None
    if normalized == "A":
        return set(available)
    shortcuts = {
        "P": {"images"},
        "V": {"videos"},
        "PV": {"images", "videos"},
        "VP": {"images", "videos"},
        "AU": {"audio"},
        "AUDIO": {"audio"},
        "D": {"documents"},
        "DOC": {"documents"},
        "AR": {"archives_installers"},
        "O": {"other"},
    }
    if normalized in shortcuts:
        selected = shortcuts[normalized].intersection(available)
        if not selected:
            raise ValueError("The requested media category is not present in this selection.")
        return selected
    symbolic_tokens = answer.upper().replace(",", " ").split()
    if symbolic_tokens and all(token in shortcuts for token in symbolic_tokens):
        selected = set().union(*(shortcuts[token] for token in symbolic_tokens)).intersection(available)
        if not selected:
            raise ValueError("The requested media categories are not present in this selection.")
        return selected
    numbers = {
        int(token) for token in answer.replace(",", " ").split()
        if token.isdigit() and 1 <= int(token) <= len(available)
    }
    if not numbers:
        raise ValueError(
            "Enter P, V, AU, D, AR, O, A, X, or displayed category numbers."
        )
    return {available[number - 1] for number in numbers}


def parse_resume_selection(answer: str, maximum: int) -> int | None:
    """Return a zero-based resume row; S/blank means skip."""
    normalized = answer.strip().upper().replace(" ", "")
    if normalized in {"", "S", "X"}:
        return None
    if normalized.startswith("R"):
        normalized = normalized[1:]
    if normalized.isdigit() and 1 <= int(normalized) <= maximum:
        return int(normalized) - 1
    raise ValueError("Enter a displayed job number (for example 1 or R1), or S to skip.")


@dataclass(frozen=True, slots=True)
class SourceFile:
    source_path: str
    relative_path: str
    name: str
    size: int
    mtime_ns: int
    extension: str
    category: str
    major_folder: str
    parent_folder: str


def collect_filesystem_sources(paths: Iterable[Path]) -> list[SourceFile]:
    result: list[SourceFile] = []
    for requested in paths:
        source = requested.resolve()
        if source.is_file():
            result.append(_filesystem_entry(source, Path(source.name)))
            continue
        if not source.is_dir():
            raise FileNotFoundError(source)
        prefix = Path(source.name or source.anchor.rstrip("\\/"))
        for path in iter_files(source):
            result.append(_filesystem_entry(path, prefix / path.relative_to(source)))
    return result


def _filesystem_entry(path: Path, relative: Path) -> SourceFile:
    stat = path.stat()
    extension, category = classify_name(path.name)
    relative_text = relative.as_posix()
    return SourceFile(
        source_path=str(path),
        relative_path=relative_text,
        name=path.name,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        extension=extension,
        category=category,
        major_folder=major_folder(relative_text),
        parent_folder=relative.parent.as_posix() if relative.parent.as_posix() != "." else "Root files",
    )


def mtp_sources(entries: Iterable[MtpEntry]) -> list[SourceFile]:
    result: list[SourceFile] = []
    for entry in entries:
        parent = PurePosixPath(entry.logical_path).parent.as_posix()
        result.append(SourceFile(
            source_path=entry.logical_path,
            relative_path=entry.logical_path,
            name=entry.name,
            size=entry.size,
            mtime_ns=entry.mtime_ns,
            extension=entry.extension,
            category=entry.category,
            major_folder=major_folder(entry.logical_path),
            parent_folder=parent if parent != "." else "Root files",
        ))
    return result


def filter_sources(
    entries: Iterable[SourceFile],
    categories: set[str],
    excluded_major: set[str],
    excluded_folders: set[str],
    excluded_files: set[str] | None = None,
) -> list[SourceFile]:
    excluded_files = excluded_files or set()
    return [
        entry for entry in entries
        if entry.category in categories
        and entry.major_folder not in excluded_major
        and entry.parent_folder not in excluded_folders
        and entry.source_path not in excluded_files
    ]


def summarize(entries: Iterable[SourceFile], attribute: str) -> list[tuple[str, int, int]]:
    totals: dict[str, tuple[int, int]] = {}
    for entry in entries:
        key = str(getattr(entry, attribute))
        count, byte_count = totals.get(key, (0, 0))
        totals[key] = (count + 1, byte_count + entry.size)
    return sorted(
        ((key, count, byte_count) for key, (count, byte_count) in totals.items()),
        key=lambda item: (-item[2], item[0].lower()),
    )


def _safe_destination_filename(name: str, extension: str) -> str:
    cleaned = "".join(
        "_" if character in '<>:"/\\|?*' or ord(character) < 32 else character
        for character in name
    ).strip(" .")
    if not cleaned:
        cleaned = f"unnamed{extension}"
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
    if PurePosixPath(cleaned).stem.upper() in reserved:
        cleaned = f"_{cleaned}"
    return cleaned


def categorize_destinations(
    entries: Iterable[SourceFile],
    device_label: str = "",
    destination_folders: dict[tuple[str, str], str] | None = None,
) -> list[SourceFile]:
    """Flatten selected files directly into their category folder."""
    destination_folders = destination_folders or {}
    result: list[SourceFile] = []
    for entry in entries:
        folder = destination_folders.get(
            (entry.parent_folder, entry.category),
            CATEGORY_DESTINATIONS[entry.category],
        )
        destination = (
            PurePosixPath(folder)
            / _safe_destination_filename(entry.name, entry.extension)
        )
        result.append(SourceFile(
            source_path=entry.source_path,
            relative_path=destination.as_posix(),
            name=entry.name,
            size=entry.size,
            mtime_ns=entry.mtime_ns,
            extension=entry.extension,
            category=entry.category,
            major_folder=entry.major_folder,
            parent_folder=entry.parent_folder,
        ))
    return result
