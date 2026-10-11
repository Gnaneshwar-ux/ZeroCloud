from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path


SETTINGS_FILENAME = "disk-backup-settings.json"
SETTINGS_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class StateConfiguration:
    state_root: Path
    pending_copy_from: Path | None = None


def default_state_root(app_root: Path) -> Path:
    return app_root.resolve() / "state"


def settings_path(app_root: Path) -> Path:
    return app_root.resolve() / SETTINGS_FILENAME


def resolve_state_path(app_root: Path, value: str) -> Path:
    return _resolve_path(app_root, value)


def configured_state(app_root: Path) -> StateConfiguration | None:
    override = os.environ.get("DISK_BACKUP_STATE")
    if override:
        return StateConfiguration(_resolve_path(app_root, override))
    path = settings_path(app_root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Application settings are unreadable: {path}: {exc}") from exc
    if int(payload.get("schema_version", 0)) != SETTINGS_SCHEMA_VERSION:
        raise RuntimeError(f"Unsupported application settings version in {path}.")
    state_value = str(payload.get("state_path", "")).strip()
    if not state_value:
        raise RuntimeError(f"Application settings do not contain a state path: {path}")
    pending_value = str(payload.get("pending_copy_from", "")).strip()
    return StateConfiguration(
        _resolve_path(app_root, state_value),
        _resolve_path(app_root, pending_value) if pending_value else None,
    )


def save_state_configuration(
    app_root: Path,
    state_root: Path,
    *,
    pending_copy_from: Path | None = None,
) -> Path:
    app_root = app_root.resolve()
    state_root = state_root.resolve()
    payload: dict[str, object] = {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "state_path": _stored_path(app_root, state_root),
    }
    if pending_copy_from is not None:
        payload["pending_copy_from"] = _stored_path(app_root, pending_copy_from.resolve())
    destination = settings_path(app_root)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"Could not save application settings beside the executable: {destination}: {exc}"
        ) from exc
    return destination


def activate_configured_state(app_root: Path) -> StateConfiguration | None:
    configuration = configured_state(app_root)
    if configuration is None or configuration.pending_copy_from is None:
        return configuration
    copy_state_directory(configuration.pending_copy_from, configuration.state_root)
    save_state_configuration(app_root, configuration.state_root)
    return StateConfiguration(configuration.state_root)


def validate_state_destination(source: Path, destination: Path) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    if destination == source:
        return destination
    if destination == Path(destination.anchor):
        raise ValueError("Choose a dedicated folder, not the root of a drive.")
    if source in destination.parents or destination in source.parents:
        raise ValueError("The new state folder cannot contain or be inside the current state folder.")
    if destination.exists() and not destination.is_dir():
        raise ValueError("The selected state location is a file, not a folder.")
    return destination


def state_directory_is_empty(path: Path) -> bool:
    return not path.exists() or not any(path.iterdir())


def copy_state_directory(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = validate_state_destination(source, destination)
    if not source.is_dir():
        raise FileNotFoundError(f"Current state folder is missing: {source}")
    if not state_directory_is_empty(destination):
        raise RuntimeError(
            f"The new state folder is not empty: {destination}. Choose an empty folder."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.migration-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, temporary)
        database = temporary / "catalog.sqlite3"
        if database.is_file():
            connection = sqlite3.connect(database)
            try:
                result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            finally:
                connection.close()
            if result.lower() != "ok":
                raise RuntimeError(f"Copied catalog integrity check failed: {result}")
        if destination.exists():
            destination.rmdir()
        temporary.replace(destination)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def _stored_path(app_root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(app_root)
    except ValueError:
        return str(path)
    return relative.as_posix() or "."


def _resolve_path(app_root: Path, value: str) -> Path:
    expanded = Path(os.path.expandvars(value)).expanduser()
    if not expanded.is_absolute():
        expanded = app_root.resolve() / expanded
    return expanded.resolve()
