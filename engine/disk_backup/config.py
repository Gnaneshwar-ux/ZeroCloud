from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "Disk Backup Manager"
BUFFER_SIZE = 4 * 1024 * 1024


def default_workers() -> int:
    """Use at most roughly 60% of logical CPUs, never fewer than one."""
    logical = os.cpu_count() or 1
    return max(1, int(logical * 0.60))


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    state: Path
    database: Path
    logs: Path
    staging: Path

    @classmethod
    def from_root(cls, root: Path) -> "AppPaths":
        return cls.from_state_root(root, root.resolve() / "state")

    @classmethod
    def from_state_root(cls, root: Path, state: Path) -> "AppPaths":
        root = root.resolve()
        state = state.resolve()
        return cls(
            root=root,
            state=state,
            database=state / "catalog.sqlite3",
            logs=state / "logs",
            staging=state / "staging",
        )

    def ensure(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)


def application_root() -> Path:
    override = os.environ.get("DISK_BACKUP_HOME")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        executable_root = Path(sys.executable).resolve().parent
        # Once the portable executable has its own state setting, it is the
        # canonical application root even when a legacy project-level state
        # folder is retained as a recovery copy.
        if (executable_root / "disk-backup-settings.json").is_file():
            return executable_root
        project_root = executable_root.parent
        if executable_root.name.lower() == "dist" and (project_root / "state" / "catalog.sqlite3").exists():
            return project_root
        return executable_root
    return Path(__file__).resolve().parents[2]


def default_vault_root(app_root: Path | None = None) -> Path:
    """Place the default Media vault on the same volume as the application."""
    root = (app_root or application_root()).resolve()
    anchor = root.anchor
    return Path(anchor) / "Media" if anchor else root / "Media"
