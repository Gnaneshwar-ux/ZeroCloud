from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .media import classify_name


@dataclass(slots=True)
class MtpEntry:
    logical_path: str
    name: str
    size: int
    mtime_ns: int
    extension: str
    category: str
    type_name: str
    item: object


@dataclass(slots=True)
class MtpLocation:
    device_id: str
    label: str
    device_item: object
    storage_item: object


def _shell():
    if os.name != "nt":
        raise RuntimeError("MTP sources are supported only on Windows.")
    from win32com.client import Dispatch

    return Dispatch("Shell.Application")


def open_location(device_id: str) -> MtpLocation:
    if not device_id.startswith("mtp:"):
        raise ValueError("Not an MTP device identifier.")
    opaque, storage_name = device_id[4:].rsplit("|", 1)
    shell = _shell()
    this_pc = shell.Namespace(17)
    for device in this_pc.Items():
        if str(device.Path) != opaque:
            continue
        for storage in device.GetFolder.Items():
            if str(storage.Name) == storage_name:
                return MtpLocation(device_id, f"{device.Name} / {storage.Name}", device, storage)
    raise RuntimeError("The portable device is no longer connected or unlocked.")


def list_children(device_id: str, relative_path: str = "") -> list[tuple[str, bool, int]]:
    location = open_location(device_id)
    item = resolve_item(location.storage_item, relative_path)
    result: list[tuple[str, bool, int]] = []
    for child in item.GetFolder.Items():
        size = 0
        try:
            size = int(child.Size)
        except Exception:
            pass
        result.append((str(child.Name), bool(child.IsFolder), size))
    return result


def resolve_item(storage_item: object, relative_path: str) -> object:
    current = storage_item
    parts = [part for part in PurePosixPath(relative_path).parts if part not in ("", ".")]
    for part in parts:
        namespace = current.GetFolder
        match = next(
            (child for child in namespace.Items()
             if str(child.Name) == part or (not child.IsFolder and _canonical_name(child, namespace)[0] == part)),
            None,
        )
        if match is None:
            raise FileNotFoundError(relative_path)
        current = match
    return current


def collect_entries(device_id: str, selected_paths: Iterable[str]) -> list[MtpEntry]:
    groups = collect_entry_groups(device_id, selected_paths)
    return [entry for _, entries in groups for entry in entries]


InventoryProgress = Callable[[str, int, int], None]


def collect_entry_groups(
    device_id: str,
    selected_paths: Iterable[str],
    progress: InventoryProgress | None = None,
) -> list[tuple[str, list[MtpEntry]]]:
    """Inventory selected MTP paths once while preserving their top-level groups."""
    location = open_location(device_id)
    groups: list[tuple[str, list[MtpEntry]]] = []
    total_files = 0
    total_bytes = 0
    for selected in selected_paths:
        entries: list[MtpEntry] = []

        def record(entry: MtpEntry) -> None:
            nonlocal total_files, total_bytes
            total_files += 1
            total_bytes += entry.size
            if progress and (total_files == 1 or total_files % 25 == 0):
                progress(selected, total_files, total_bytes)

        item = resolve_item(location.storage_item, selected)
        if item.IsFolder:
            _walk(item, PurePosixPath(selected), entries, record)
        else:
            parent_path = PurePosixPath(selected).parent.as_posix()
            parent = resolve_item(location.storage_item, "" if parent_path == "." else parent_path)
            entry = _entry(item, PurePosixPath(selected), parent.GetFolder)
            entries.append(entry)
            record(entry)
        groups.append((selected, entries))
        if progress:
            progress(selected, total_files, total_bytes)
    return groups


def _walk(
    folder_item: object,
    prefix: PurePosixPath,
    output: list[MtpEntry],
    on_entry: Callable[[MtpEntry], None] | None = None,
) -> None:
    namespace = folder_item.GetFolder
    for child in namespace.Items():
        path = prefix / str(child.Name)
        if child.IsFolder:
            _walk(child, path, output, on_entry)
        else:
            entry = _entry(child, path, namespace)
            output.append(entry)
            if on_entry:
                on_entry(entry)


def _entry(item: object, path: PurePosixPath, namespace: object) -> MtpEntry:
    size = _item_size(item, namespace)
    mtime_ns = 0
    try:
        modified = item.ModifyDate
        if hasattr(modified, "timestamp"):
            mtime_ns = int(modified.timestamp() * 1_000_000_000)
    except Exception:
        pass
    canonical_name, extension = _canonical_name(item, namespace)
    logical = (path.parent / canonical_name).as_posix()
    _, category = classify_name(canonical_name, extension)
    type_name = ""
    try:
        type_name = str(namespace.GetDetailsOf(item, 1))
    except Exception:
        pass
    return MtpEntry(logical, canonical_name, size, mtime_ns, extension, category, type_name, item)


def _item_size(item: object, namespace: object) -> int:
    """Read an MTP file size using multiple Windows Shell representations."""
    values: list[object] = []
    try:
        values.append(item.ExtendedProperty("System.Size"))
    except Exception:
        pass
    try:
        values.append(item.Size)
    except Exception:
        pass
    try:
        values.append(namespace.GetDetailsOf(item, 2))
    except Exception:
        pass
    for value in values:
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
        text = str(value or "").strip()
        if text.replace(",", "").isdigit():
            return int(text.replace(",", ""))
        parsed = _parse_display_size(text)
        if parsed:
            return parsed
    return 0


def _canonical_name(item: object, namespace: object) -> tuple[str, str]:
    display_name = str(item.Name)
    filename = ""
    extension_hint = ""
    try:
        filename = str(item.ExtendedProperty("System.FileName") or "")
    except Exception:
        pass
    try:
        extension_hint = str(item.ExtendedProperty("System.FileExtension") or "")
    except Exception:
        pass
    extension, _ = classify_name(filename or display_name, extension_hint)
    canonical = filename or display_name
    if extension and not canonical.lower().endswith(extension):
        canonical += extension
    return canonical, extension


def _parse_display_size(value: str) -> int:
    parts = value.replace(",", "").split()
    if len(parts) != 2:
        return 0
    scales = {"bytes": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
    try:
        return int(float(parts[0]) * scales[parts[1].lower()])
    except (ValueError, KeyError):
        return 0


def copy_entry_to(entry: MtpEntry, directory: Path, timeout_seconds: float | None = None) -> Path:
    """Read a portable-device item into a local staging folder through Windows Shell."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / entry.name
    if destination.exists():
        destination.unlink()
    namespace = _shell().Namespace(str(directory.resolve()))
    if namespace is None:
        raise RuntimeError(f"Windows could not open staging directory {directory}")
    # FOF_NO_UI: silent + no confirmation + no mkdir confirmation + no error UI.
    # Some vendor MTP shell extensions may still choose to display native progress.
    namespace.CopyHere(entry.item, 0x0614)
    timeout = timeout_seconds or max(120.0, entry.size / (5 * 1024 * 1024) + 60.0)
    deadline = time.monotonic() + timeout
    last_size = -1
    stable_since = time.monotonic()
    required_stability = 0.5 if entry.size > 0 else 2.0
    while time.monotonic() < deadline:
        if destination.is_file():
            current_size = destination.stat().st_size
            tolerance = max(1024, int(entry.size * 0.005))
            size_matches = entry.size == 0 or abs(current_size - entry.size) <= tolerance
            if (
                size_matches
                and current_size == last_size
                and time.monotonic() - stable_since >= required_stability
            ):
                return destination
            if current_size != last_size:
                last_size = current_size
                stable_since = time.monotonic()
        time.sleep(0.25)
    raise TimeoutError(f"Timed out reading {entry.logical_path} from the portable device.")


def ensure_folder(device_id: str, relative_path: str, timeout_seconds: float = 30.0) -> object:
    """Resolve or create an MTP folder hierarchy and return its Shell item."""
    parts = [part for part in PurePosixPath(relative_path).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("Portable-device target paths cannot contain '..'.")
    location = open_location(device_id)
    current = location.storage_item
    built: list[str] = []
    for part in parts:
        built.append(part)
        namespace = current.GetFolder
        match = next(
            (child for child in namespace.Items() if child.IsFolder and str(child.Name) == part),
            None,
        )
        if match is None:
            namespace.NewFolder(part)
            deadline = time.monotonic() + timeout_seconds
            folder_path = PurePosixPath(*built).as_posix()
            while time.monotonic() < deadline:
                try:
                    location = open_location(device_id)
                    match = resolve_item(location.storage_item, folder_path)
                    if match.IsFolder:
                        break
                except FileNotFoundError:
                    pass
                time.sleep(0.25)
            else:
                raise TimeoutError(f"Windows did not create device folder {folder_path}.")
        current = match
    return current


def copy_file_to_device(
    device_id: str,
    source: Path,
    logical_path: str,
    timeout_seconds: float | None = None,
) -> MtpEntry:
    """Copy one local file to an exact, currently absent MTP path and wait for completion."""
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    target = PurePosixPath(logical_path)
    if target.name != source.name:
        raise ValueError("The staged filename must match the requested device filename.")
    if any(part == ".." for part in target.parts):
        raise ValueError("Portable-device target paths cannot contain '..'.")
    parent_text = "" if target.parent.as_posix() == "." else target.parent.as_posix()
    try:
        location = open_location(device_id)
        resolve_item(location.storage_item, logical_path)
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError(logical_path)

    parent_item = ensure_folder(device_id, parent_text)
    destination = parent_item.GetFolder
    local_namespace = _shell().Namespace(str(source.parent))
    if local_namespace is None:
        raise RuntimeError(f"Windows could not open restore staging folder {source.parent}")
    local_item = local_namespace.ParseName(source.name)
    if local_item is None:
        raise RuntimeError(f"Windows could not resolve staged restore file {source}")
    destination.CopyHere(local_item, 0x0614)

    timeout = timeout_seconds or max(120.0, source.stat().st_size / (5 * 1024 * 1024) + 60.0)
    deadline = time.monotonic() + timeout
    last_size = -1
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        try:
            location = open_location(device_id)
            item = resolve_item(location.storage_item, logical_path)
            location_parent = ensure_folder(device_id, parent_text)
            namespace = location_parent.GetFolder
            size = _item_size(item, namespace)
            if size == source.stat().st_size and size == last_size and time.monotonic() - stable_since >= 1.0:
                return _entry(item, target, namespace)
            if size != last_size:
                last_size = size
                stable_since = time.monotonic()
        except FileNotFoundError:
            pass
        time.sleep(0.25)
    raise TimeoutError(f"Timed out restoring {logical_path} to the portable device.")


def open_entry(device_id: str, logical_path: str) -> None:
    """Ask Windows Shell to open an MTP item with its registered default application."""
    location = open_location(device_id)
    item = resolve_item(location.storage_item, logical_path)
    try:
        item.InvokeVerb()
    except Exception:
        item.InvokeVerb("open")


def delete_entry(device_id: str, logical_path: str, timeout_seconds: float = 30.0) -> None:
    """Permanently delete one re-resolved MTP item and confirm it disappeared."""
    location = open_location(device_id)
    item = resolve_item(location.storage_item, logical_path)
    if item.IsFolder:
        raise ValueError("Folder deletion is not supported by verified cleanup.")
    _delete_shell_item_silently(item)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            location = open_location(device_id)
            resolve_item(location.storage_item, logical_path)
        except FileNotFoundError:
            return
        time.sleep(0.5)
    raise TimeoutError(f"Windows did not confirm deletion of {logical_path}.")


def resolve_delete_entries(
    device_id: str, logical_paths: Iterable[str],
) -> tuple[list[MtpEntry], dict[str, str]]:
    """Resolve multiple MTP files through one Shell connection.

    Resolving a portable-device path is comparatively expensive. Bulk cleanup uses
    this helper so it does not reconnect to the device for every file.
    """
    location = open_location(device_id)
    entries: list[MtpEntry] = []
    errors: dict[str, str] = {}
    for logical_path in logical_paths:
        try:
            item = resolve_item(location.storage_item, logical_path)
            if item.IsFolder:
                raise ValueError("Folder deletion is not supported by verified cleanup.")
            parent_path = PurePosixPath(logical_path).parent.as_posix()
            parent = resolve_item(
                location.storage_item, "" if parent_path == "." else parent_path,
            )
            entries.append(_entry(item, PurePosixPath(logical_path), parent.GetFolder))
        except Exception as exc:
            errors[logical_path] = str(exc)
    return entries, errors


def delete_resolved_entries(
    device_id: str, entries: Iterable[MtpEntry], timeout_seconds: float = 30.0,
) -> tuple[set[str], set[str]]:
    """Delete an already-resolved MTP batch and return (deleted, still_present).

    One IFileOperation is substantially faster than creating a Windows Shell
    operation for every file. Results are still confirmed by re-enumerating each
    affected parent folder before callers record a file as deleted.
    """
    batch = list(entries)
    if not batch:
        return set(), set()
    _delete_shell_items_silently([entry.item for entry in batch])
    requested = {entry.logical_path for entry in batch}
    deadline = time.monotonic() + timeout_seconds
    remaining = requested
    while time.monotonic() < deadline:
        remaining = _existing_paths(device_id, requested)
        if not remaining:
            return requested, set()
        time.sleep(0.5)
    return requested - remaining, remaining


def _existing_paths(device_id: str, logical_paths: Iterable[str]) -> set[str]:
    """Check candidate existence with one enumeration per parent folder."""
    location = open_location(device_id)
    grouped: dict[str, set[str]] = {}
    for logical_path in logical_paths:
        path = PurePosixPath(logical_path)
        parent = "" if path.parent.as_posix() == "." else path.parent.as_posix()
        grouped.setdefault(parent, set()).add(path.name)

    existing: set[str] = set()
    for parent_path, candidates in grouped.items():
        try:
            parent = resolve_item(location.storage_item, parent_path)
            namespace = parent.GetFolder
            names: set[str] = set()
            for child in namespace.Items():
                visible = str(child.Name)
                names.add(visible)
                if not child.IsFolder:
                    names.add(_canonical_name(child, namespace)[0])
            prefix = PurePosixPath(parent_path) if parent_path else PurePosixPath()
            existing.update((prefix / name).as_posix() for name in candidates & names)
        except FileNotFoundError:
            continue
    return existing


def _delete_shell_item_silently(item: object) -> None:
    """Delete a Shell namespace item through IFileOperation without native confirmation UI."""
    _delete_shell_items_silently([item])


def _delete_shell_items_silently(items: Iterable[object]) -> None:
    """Queue multiple Shell namespace items in one silent IFileOperation."""
    if os.name != "nt":
        raise RuntimeError("Silent Shell deletion is supported only on Windows.")
    import pythoncom
    from win32com.shell import shell, shellcon

    operation = pythoncom.CoCreateInstance(
        shell.CLSID_FileOperation, None, pythoncom.CLSCTX_ALL, shell.IID_IFileOperation,
    )
    flags = (
        shellcon.FOF_NOCONFIRMATION
        | shellcon.FOF_SILENT
        | shellcon.FOF_NOERRORUI
        | shellcon.FOF_NOCONFIRMMKDIR
    )
    operation.SetOperationFlags(flags)
    queued = 0
    for item in items:
        pidl = shell.SHGetIDListFromObject(item)
        shell_item = shell.SHCreateItemFromIDList(pidl, shell.IID_IShellItem)
        operation.DeleteItem(shell_item, None)
        queued += 1
    if not queued:
        return
    operation.PerformOperations()
    if operation.GetAnyOperationsAborted():
        raise RuntimeError("Windows aborted the silent portable-device deletion.")
