from __future__ import annotations

import ctypes
import os
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    label: str
    kind: str
    root: str
    total_bytes: int | None
    free_bytes: int | None

    @property
    def is_filesystem(self) -> bool:
        return self.kind in {"fixed", "removable", "network"}


def _volume_information(root: str) -> tuple[str, int]:
    if os.name != "nt":
        return Path(root).name or root, abs(hash(Path(root).resolve())) & 0xFFFFFFFF
    volume_name = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_uint32()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root), volume_name, len(volume_name), ctypes.byref(serial),
        None, None, None, 0,
    )
    return (volume_name.value or root.rstrip("\\"), serial.value) if ok else (root, 0)


def filesystem_devices() -> list[Device]:
    devices: list[Device] = []
    if os.name == "nt":
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        roots = [f"{letter}:\\" for index, letter in enumerate(string.ascii_uppercase) if mask & (1 << index)]
    else:
        roots = ["/"]
    drive_kinds = {2: "removable", 3: "fixed", 4: "network"}
    for root in roots:
        try:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root) if os.name == "nt" else 3
            if drive_type not in drive_kinds:
                continue
            usage = os.statvfs(root) if os.name != "nt" else None
            if usage:
                total = usage.f_blocks * usage.f_frsize
                free = usage.f_bavail * usage.f_frsize
            else:
                import shutil
                disk_usage = shutil.disk_usage(root)
                total, free = disk_usage.total, disk_usage.free
            label, serial = _volume_information(root)
            devices.append(Device(f"winvol:{serial:08X}", label, drive_kinds[drive_type], root, total, free))
        except (OSError, PermissionError):
            continue
    return devices


def mtp_devices() -> list[Device]:
    """Discover Windows Portable Devices through the read-only Shell namespace."""
    if os.name != "nt":
        return []
    try:
        from win32com.client import Dispatch
        shell = Dispatch("Shell.Application")
        this_pc = shell.Namespace(17)
    except Exception:
        return []
    found: list[Device] = []
    for item in this_pc.Items():
        try:
            if str(item.Type) != "Portable Device":
                continue
            for storage in item.GetFolder.Items():
                total = _parse_windows_size(item.GetFolder.GetDetailsOf(storage, 2))
                free = _parse_windows_size(item.GetFolder.GetDetailsOf(storage, 3))
                found.append(Device(
                    id=f"mtp:{item.Path}|{storage.Name}",
                    label=f"{item.Name} / {storage.Name}",
                    kind="mtp",
                    root=f"{item.Name}/{storage.Name}",
                    total_bytes=total,
                    free_bytes=free,
                ))
        except Exception:
            continue
    return found


def connected_devices() -> list[Device]:
    return filesystem_devices() + mtp_devices()


def find_device(device_id: str) -> Device | None:
    return next((device for device in connected_devices() if device.id == device_id), None)


def _parse_windows_size(value: str) -> int | None:
    parts = str(value).replace(",", "").split()
    if len(parts) != 2:
        return None
    scales = {"bytes": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
    try:
        return int(float(parts[0]) * scales[parts[1].lower()])
    except (ValueError, KeyError):
        return None


def iter_files(root: Path, excluded_roots: tuple[Path, ...] = ()) -> Iterator[Path]:
    excluded = {path.resolve() for path in excluded_roots}
    def inaccessible(error: OSError) -> None:
        raise error  # An incomplete walk must not erase unverified catalog entries.
    for directory, subdirectories, filenames in os.walk(root, topdown=True, followlinks=False, onerror=inaccessible):
        current = Path(directory)
        subdirectories[:] = [
            name for name in subdirectories
            if (current / name).resolve() not in excluded
            and not (current / name).is_symlink()
            and name.lower() not in {'$recycle.bin','system volume information','.vela-cache'}
            and not (current == Path(root.anchor) and name.lower() in {'windows','program files','program files (x86)','programdata'})
            and not any((current / name / marker).exists() for marker in ['vela-folder-cache.json','vela-library.json'])
        ]
        for filename in filenames:
            path = current / filename
            try:
                if not path.is_symlink():
                    yield path
            except OSError:
                continue
