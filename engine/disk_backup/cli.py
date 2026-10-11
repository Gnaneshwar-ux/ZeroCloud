from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from . import __version__
from .backup import BackupManager, estimate_selected, validate_target_location
from .cleanup import CleanupManager
from .config import APP_NAME, AppPaths, application_root, default_vault_root, default_workers
from .database import Catalog, utc_now
from .devices import Device, connected_devices
from .indexer import estimate_scan, index_volume
from .mtp import MtpEntry, collect_entries, collect_entry_groups, list_children, open_entry
from .media import CATEGORY_DESTINATIONS, CATEGORY_LABELS
from .restore import RestoreManager
from .search import (
    SearchResult, index_health, open_search_results, reveal_stored_file, search_catalog,
)
from .selection import (
    SourceFile, apply_category_selection, apply_numbered_selection, categorize_destinations,
    collect_filesystem_sources, filter_sources, parse_resume_selection,
    mtp_sources, summarize,
)
from .state_config import (
    activate_configured_state, default_state_root, resolve_state_path,
    save_state_configuration, settings_path, state_directory_is_empty,
    validate_state_destination,
)


console = Console()

ALL_CATEGORIES = set(CATEGORY_LABELS)


def parse_number_selection(value: str, maximum: int) -> set[int]:
    result: set[int] = set()
    for token in value.replace(",", " ").split():
        if token.isdigit() and 1 <= int(token) <= maximum:
            result.add(int(token))
    return result


def pause_before_menu() -> None:
    Prompt.ask("Press Enter to return to the main menu", default="", show_default=False)


def human_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def show_devices(devices: list[Device]) -> None:
    table = Table(title="Connected storage")
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Kind")
    table.add_column("Location")
    table.add_column("Used / Total", justify="right")
    for number, device in enumerate(devices, 1):
        used = None if device.total_bytes is None or device.free_bytes is None else device.total_bytes - device.free_bytes
        table.add_row(str(number), device.label, device.kind, device.root,
                      f"{human_bytes(used)} / {human_bytes(device.total_bytes)}")
    console.print(table)


def choose_device(
    devices: list[Device],
    prompt: str,
    *,
    filesystem_only: bool = False,
    kind: str | None = None,
) -> Device:
    candidates = [device for device in devices if device.is_filesystem] if filesystem_only else devices
    if kind is not None:
        candidates = [device for device in candidates if device.kind == kind]
    if not candidates:
        raise RuntimeError("No suitable connected storage was found.")
    show_devices(candidates)
    number = IntPrompt.ask(prompt, choices=[str(index) for index in range(1, len(candidates) + 1)])
    return candidates[number - 1]


def do_index(catalog: Catalog, paths: AppPaths, device: Device, full: bool, assume_yes: bool) -> None:
    if not device.is_filesystem:
        raise ValueError("Choose a regular disk as the index target; phones are supported as backup sources.")
    estimate = estimate_scan(catalog, device, full)
    basis = "catalog history" if estimate.known_from_cache else "used disk capacity"
    console.print(Panel(
        f"Mode: {'complete rescan' if full else 'incremental update'}\n"
        f"Expected data: {human_bytes(estimate.bytes)}\n"
        f"Estimated time: {human_duration(estimate.seconds)} ({basis})\n"
        f"Hash workers: {default_workers()} (maximum 60% of logical CPUs)",
        title="Index preflight",
    ))
    if not assume_yes and not Confirm.ask("Start the index operation?", default=False):
        console.print("Cancelled before making changes.")
        return
    excluded = (paths.root.resolve(),) if Path(device.root).resolve() in paths.root.resolve().parents or Path(device.root).resolve() == paths.root.resolve() else ()
    with Progress(
        TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(),
        TextColumn("{task.fields[details]}"), TimeRemainingColumn(), console=console,
    ) as progress:
        total = estimate.files or None
        task = progress.add_task("Indexing", total=total, details="starting")

        def update(files: int, byte_count: int, hashed: int) -> None:
            progress.update(task, completed=files, details=f"{files:,} files | {human_bytes(byte_count)} | hashed {human_bytes(hashed)}")

        result = index_volume(catalog, device, full=full, excluded_roots=excluded, progress=update)
    console.print(
        f"Index complete: {result.files:,} files, {human_bytes(result.bytes)}, "
        f"{result.hashed_files:,} hashed, {result.removed_entries:,} stale entries removed "
        f"in {human_duration(result.elapsed_seconds)}."
    )
    if result.errors:
        console.print(f"[yellow]{len(result.errors)} files could not be read; see scan details in the catalog.[/yellow]")


def do_backup(catalog: Catalog, paths: AppPaths, source_paths: list[Path], target: Device,
              target_path: Path, assume_yes: bool) -> None:
    validate_target_location(source_paths, target_path)
    source_entries = collect_filesystem_sources(source_paths)
    source_label = source_paths[0].drive or source_paths[0].name or "Device"
    planned = categorize_destinations(source_entries, source_label)
    estimate = estimate_selected(catalog, planned)
    free = shutil.disk_usage(target.root).free
    if estimate.bytes > free:
        raise RuntimeError(
            f"Worst-case backup size {human_bytes(estimate.bytes)} exceeds target free space {human_bytes(free)}."
        )
    console.print(Panel(
        f"Sources: {len(source_paths)} selected path(s)\nFiles: {estimate.files:,}\n"
        f"Data: {human_bytes(estimate.bytes)}\nTarget free: {human_bytes(free)}\n"
        f"Estimated time: {human_duration(estimate.seconds)}\n"
        "Exact SHA-256 duplicates will be skipped and recorded.",
        title="Backup preflight",
    ))
    if not assume_yes and not Confirm.ask("Create and start this resumable backup job?", default=False):
        console.print("Cancelled before copying files.")
        return
    manager = BackupManager(catalog, paths.staging)
    job_id = manager.create_job(source_paths, target, target_path, included_entries=planned)
    console.print(f"Backup ID: [bold]{job_id}[/bold]")
    run_backup_job(manager, catalog, job_id)


def do_mtp_backup(catalog: Catalog, paths: AppPaths, source: Device, selected_paths: list[str],
                  target: Device, target_path: Path, assume_yes: bool) -> None:
    console.print(Panel(
        "Windows must first enumerate the selected phone folders. MTP does not expose a reliable "
        "instant file count, so the first-run estimate is unknown. This metadata pass is read-only.",
        title="Phone inventory preflight",
    ))
    if not assume_yes and not Confirm.ask("Inventory the selected phone folders?", default=False):
        console.print("Cancelled before reading phone folders.")
        return
    with console.status("Reading phone folder metadata over USB/MTP..."):
        entries = collect_entries(source.id, selected_paths)
    planned = categorize_destinations(mtp_sources(entries), source.label)
    byte_count = sum(entry.size for entry in planned)
    free = shutil.disk_usage(target.root).free
    if byte_count > free:
        raise RuntimeError(
            f"Worst-case phone backup size {human_bytes(byte_count)} exceeds target free space {human_bytes(free)}."
        )
    estimate = estimate_selected(catalog, planned, "mtp")
    console.print(Panel(
        f"Phone: {source.label}\nSelected: {', '.join(selected_paths)}\nFiles: {len(entries):,}\n"
        f"Data: {human_bytes(byte_count)}\nTarget free: {human_bytes(free)}\n"
        f"Estimated copy time: {human_duration(estimate.seconds)}\n"
        "Files are staged locally, SHA-256 checked, and exact duplicates are skipped.",
        title="Phone backup preflight",
    ))
    if not assume_yes and not Confirm.ask("Create and start this resumable phone backup?", default=False):
        console.print("Cancelled before copying files.")
        return
    manager = BackupManager(catalog, paths.staging)
    job_id = manager.create_mtp_job(
        source.id, selected_paths, entries, target, target_path, included_files=planned,
    )
    console.print(f"Backup ID: [bold]{job_id}[/bold]")
    run_backup_job(manager, catalog, job_id)


def run_backup_job(manager: BackupManager, catalog: Catalog, job_id: str) -> None:
    row = catalog.connection.execute(
        "SELECT source_kind,total_files,total_bytes,completed_files,completed_bytes "
        "FROM backup_jobs WHERE id=?", (job_id,),
    ).fetchone()
    if not row:
        raise KeyError(job_id)
    is_mtp = str(row["source_kind"]) == "mtp"
    columns = [
        TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(),
        TextColumn("{task.fields[details]}"),
    ]
    if not is_mtp:
        columns.append(TimeRemainingColumn())
    byte_rate = catalog.estimate_rate("mtp_backup_bytes", 6 * 1024 * 1024)
    file_rate = catalog.estimate_rate("mtp_backup_files", 0.8)
    with Progress(*columns, console=console) as progress:
        task = progress.add_task(
            "Preparing phone" if is_mtp else "Backing up",
            total=int(row["total_bytes"]), completed=int(row["completed_bytes"]),
            details="reconnecting and reading device metadata" if is_mtp else "starting",
            start=not is_mtp,
        )
        transfer_started = not is_mtp

        def update(done: int, total: int, byte_count: int, name: str) -> None:
            nonlocal transfer_started
            if not transfer_started:
                progress.start_task(task)
                transfer_started = True
            overall_done = min(int(row["total_files"]), int(row["completed_files"]) + done)
            details = f"{overall_done}/{int(row['total_files'])} | {name[:40]}"
            if is_mtp:
                remaining_bytes = max(0, int(row["total_bytes"]) - byte_count)
                remaining_files = max(0, total - done)
                stable_eta = max(
                    remaining_bytes / byte_rate if remaining_bytes else 0,
                    remaining_files / file_rate if remaining_files else 0,
                )
                details += f" | ETA ~{human_duration(stable_eta)}"
            progress.update(
                task, description="Backing up", completed=min(int(row["total_bytes"]), byte_count),
                details=details,
            )

        result = manager.run_job(job_id, update)
    console.print(
        f"Job {job_id}: {result.status}; copied {result.copied:,}, skipped exact duplicates "
        f"{result.skipped:,}, failed {result.failed:,}."
    )


def show_jobs(catalog: Catalog) -> None:
    rows = catalog.connection.execute("SELECT * FROM backup_jobs ORDER BY created_at DESC LIMIT 100").fetchall()
    table = Table(title="Backup jobs")
    for heading in ("ID", "Status", "Files", "Copied", "Skipped", "Failed", "Data"):
        table.add_column(heading)
    for row in rows:
        table.add_row(row["id"], row["status"], f"{row['completed_files']}/{row['total_files']}",
                      str(row["copied_files"]), str(row["skipped_files"]), str(row["failed_files"]),
                      human_bytes(row["total_bytes"]))
    console.print(table)


def _short_timestamp(value: object) -> str:
    return str(value or "").replace("T", " ")[:19]


def _backup_source_summary(row: object) -> str:
    try:
        source = json.loads(str(row["source_ref"]))
    except (ValueError, TypeError):
        return str(row["source_ref"])
    if row["source_kind"] == "mtp" and isinstance(source, dict):
        paths = ", ".join(str(path) for path in source.get("paths", [])) or "selected device paths"
        return f"{source.get('device_id', 'portable device')} — {paths}"
    if isinstance(source, list):
        return "; ".join(str(path) for path in source)
    return str(source)


def show_backup_details(catalog: Catalog, row: object) -> None:
    cleanup = catalog.connection.execute(
        """SELECT cleanup_status,COUNT(1) count,COALESCE(SUM(size),0) bytes
        FROM backup_items WHERE job_id=? AND status IN ('copied','skipped_duplicate')
        GROUP BY cleanup_status ORDER BY bytes DESC""", (row["id"],)
    ).fetchall()
    restored = catalog.connection.execute(
        """SELECT COUNT(1) jobs,COALESCE(SUM(restored_files),0) files
        FROM restore_jobs WHERE backup_job_id=?""", (row["id"],)
    ).fetchone()
    console.print(Panel(
        f"Backup ID: {row['id']}\nCreated: {_short_timestamp(row['created_at'])}\n"
        f"Last updated: {_short_timestamp(row['updated_at'])}\nStatus: {row['status']}\n\n"
        f"From: {_backup_source_summary(row)}\nTo: {row['target_root']}\n\n"
        f"Selected: {int(row['total_files']):,} files / {human_bytes(int(row['total_bytes']))}\n"
        f"Copied: {int(row['copied_files']):,}\nExact duplicates skipped: {int(row['skipped_files']):,}\n"
        f"Failed: {int(row['failed_files']):,}\nRestore jobs: {int(restored['jobs'] or 0):,} "
        f"({int(restored['files'] or 0):,} files restored)",
        title="Backup history details",
    ))
    categories = catalog.connection.execute(
        """SELECT category,COUNT(1) count,COALESCE(SUM(size),0) bytes
        FROM backup_items WHERE job_id=? GROUP BY category ORDER BY bytes DESC""",
        (row["id"],),
    ).fetchall()
    table = Table(title="Content by category")
    for heading in ("Category", "Files", "Size"):
        table.add_column(heading)
    for item in categories:
        table.add_row(
            CATEGORY_LABELS.get(item["category"], item["category"]),
            f"{int(item['count']):,}", human_bytes(int(item["bytes"])),
        )
    console.print(table)
    if cleanup:
        cleanup_table = Table(title="Current source-cleanup state")
        for heading in ("State", "Files", "Source data"):
            cleanup_table.add_column(heading)
        for item in cleanup:
            cleanup_table.add_row(
                str(item["cleanup_status"]), f"{int(item['count']):,}",
                human_bytes(int(item["bytes"])),
            )
        console.print(cleanup_table)
    try:
        selection = json.loads(str(row["selection_json"] or "{}"))
    except (ValueError, TypeError):
        selection = {}
    routes = selection.get("destination_routes", []) if isinstance(selection, dict) else []
    if routes:
        route_table = Table(title="Saved source-folder routing")
        for heading in ("Source folder", "Category", "Target folder"):
            route_table.add_column(heading)
        for route in routes:
            route_table.add_row(
                str(route.get("source_folder", "")),
                CATEGORY_LABELS.get(str(route.get("category", "")), str(route.get("category", ""))),
                str(route.get("target_folder", "")),
            )
        console.print(route_table)


def show_backup_history(catalog: Catalog) -> None:
    rows = catalog.connection.execute(
        "SELECT * FROM backup_jobs ORDER BY created_at DESC LIMIT 200"
    ).fetchall()
    if not rows:
        console.print("No backup history was found.")
        return
    table = Table(title="Backup history")
    for heading in ("#", "When", "Backup ID", "From", "To", "Status", "Files", "Size"):
        table.add_column(heading)
    for number, row in enumerate(rows, 1):
        source = _backup_source_summary(row)
        table.add_row(
            str(number), _short_timestamp(row["created_at"]), str(row["id"]),
            source[:42], str(row["target_root"])[:38], str(row["status"]),
            f"{int(row['completed_files']):,}/{int(row['total_files']):,}",
            human_bytes(int(row["total_bytes"])),
        )
    console.print(table)
    while True:
        answer = Prompt.ask("Backup number for full details, or S to return", default="S").strip().upper()
        if answer in {"S", "X", ""}:
            return
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            show_backup_details(catalog, rows[int(answer) - 1])
            return
        console.print("[yellow]Choose a displayed backup number or S.[/yellow]")


def _optional_search_date(prompt: str) -> date | None:
    while True:
        value = Prompt.ask(prompt, default="").strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            console.print("[yellow]Use YYYY-MM-DD, for example 2026-06-01, or press Enter.[/yellow]")


def _show_search_details(result: SearchResult) -> None:
    console.print(Panel(
        f"Name: {result.name}\nCategory: {CATEGORY_LABELS.get(result.category, result.category)}\n"
        f"Size: {human_bytes(result.size)}\nModified: {_short_timestamp(result.modified_at) or 'unknown'}\n"
        f"Stored location: {result.location}\nOriginal source: {result.original_source}\n"
        f"Record type: {result.source_type}\nRecorded/indexed: {_short_timestamp(result.recorded_at)}\n"
        f"SHA-256: {result.sha256 or 'not available'}",
        title="Search result details",
    ))


def _open_search_selection(selected: list[SearchResult]) -> None:
    if not selected:
        console.print("[yellow]No valid result numbers were selected.[/yellow]")
        return
    if len(selected) > 10 and not Confirm.ask(
        f"Open {len(selected)} files with their Windows default applications?", default=False,
    ):
        return
    opened, errors = open_search_results(selected)
    console.print(f"Opened {opened} file(s).")
    for error in errors[:20]:
        console.print(f"[yellow]{error}[/yellow]")
    if len(errors) > 20:
        console.print(f"[yellow]{len(errors) - 20} more files could not be opened.[/yellow]")
    if errors:
        console.print(
            "Executable and script files are not launched; use F<number> to show them in Explorer."
        )


def _review_search_results(results: list[SearchResult], title: str) -> None:
    page_size = 15
    page = 0
    while True:
        page_count = max(1, (len(results) + page_size - 1) // page_size)
        page = max(0, min(page, page_count - 1))
        start = page * page_size
        visible = results[start:start + page_size]
        table = Table(title=f"{title} - page {page + 1}/{page_count}")
        for heading in ("#", "Match", "Name", "Category", "Modified", "Size", "Stored location"):
            table.add_column(heading)
        for number, result in enumerate(visible, start + 1):
            table.add_row(
                str(number), f"{result.score * 100:.0f}%", result.name,
                CATEGORY_LABELS.get(result.category, result.category),
                (_short_timestamp(result.modified_at)[:10] or "unknown"),
                human_bytes(result.size), result.location,
            )
        console.print(table)
        console.print(
            "Number = details; O<number> = open one; O 1 3 5 = open selected; "
            "OA = open all matches; F<number> = show in folder; N/P = pages; S = finish."
        )
        answer = Prompt.ask("Search action", default="S").strip().upper()
        if answer in {"S", "X", ""}:
            return
        if answer == "N":
            page = min(page + 1, page_count - 1)
            continue
        if answer == "P":
            page = max(0, page - 1)
            continue
        if answer == "OA":
            _open_search_selection(results)
            continue
        if answer.startswith("F"):
            number_text = answer[1:].strip()
            if number_text.isdigit() and 1 <= int(number_text) <= len(results):
                try:
                    reveal_stored_file(results[int(number_text) - 1].location)
                    console.print("Opened the containing folder in Explorer.")
                except (FileNotFoundError, OSError) as exc:
                    console.print(f"[yellow]{exc}[/yellow]")
            else:
                console.print("[yellow]Use F followed by a result number, for example F1.[/yellow]")
            continue
        if answer.startswith("O"):
            numbers = parse_number_selection(answer[1:], len(results))
            _open_search_selection([results[number - 1] for number in sorted(numbers)])
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(results):
            _show_search_details(results[int(answer) - 1])
            continue
        console.print("[yellow]Use a result number, O<number>, OA, F<number>, N, P, or S.[/yellow]")


def search_files(catalog: Catalog) -> None:
    console.print(Panel(
        "Quick search checks file names and folder paths, so folder or place text such as Coorg works.\n"
        "Enter A for optional category, location and modified-date filters. Results reflect the last index or backup.",
        title="File search and open",
    ))
    first = Prompt.ask("Name, folder, or place text; A advanced filters; X return").strip()
    if first.upper() == "X" or not first:
        return
    query = first
    location_filter = ""
    categories: set[str] | None = None
    modified_from: date | None = None
    modified_to: date | None = None
    if first.upper() == "A":
        query = Prompt.ask("Name or text contains; Enter for any", default="").strip()
        location_filter = Prompt.ask(
            "Stored/original folder contains (for example WhatsApp or Coorg); Enter for any",
            default="",
        ).strip()
        available = list(CATEGORY_LABELS)
        category_table = Table(title="Optional category filter")
        category_table.add_column("#")
        category_table.add_column("Category")
        for number, category in enumerate(available, 1):
            category_table.add_row(str(number), CATEGORY_LABELS[category])
        console.print(category_table)
        category_answer = Prompt.ask(
            "Categories: P photos, V videos, AU audio, D documents, AR archives, O other, A all",
            default="A",
        )
        try:
            categories = apply_category_selection(available, category_answer)
        except ValueError as exc:
            console.print(f"[yellow]{exc} Using all categories.[/yellow]")
            categories = set(available)
        modified_from = _optional_search_date("Modified on/after YYYY-MM-DD; Enter for any")
        modified_to = _optional_search_date("Modified on/before YYYY-MM-DD; Enter for any")
        if modified_from and modified_to and modified_from > modified_to:
            console.print("[yellow]Start date is after end date; search cancelled.[/yellow]")
            return
    results = search_catalog(
        catalog, query, limit=100, categories=categories, location=location_filter,
        modified_from=modified_from, modified_to=modified_to,
    )
    if not results:
        console.print("No matching catalog records were found.")
    else:
        title = f"Matches for: {query}" if query else "Filtered file matches"
        _review_search_results(results, title)
    health = index_health(catalog)
    if health:
        table = Table(title="Index freshness")
        for heading in ("Disk", "Location", "Last successful index", "Recommendation"):
            table.add_column(heading)
        for item in health:
            table.add_row(
                item.label, item.root, _short_timestamp(item.indexed_at) or "Never",
                item.recommendation,
            )
        console.print(table)
        console.print(
            "If files changed after the displayed time, use main-menu option 2 to update that disk's index."
        )


def show_catalog_safety(catalog: Catalog) -> None:
    catalog.verify_integrity()
    backups = catalog.catalog_backups()
    journal = str(catalog.connection.execute("PRAGMA journal_mode").fetchone()[0]).upper()
    latest = str(backups[0]) if backups else "No recovery copy yet"
    console.print(Panel(
        f"Live catalog: {catalog.path}\nSize: {human_bytes(catalog.path.stat().st_size)}\n"
        f"Integrity: OK\nJournal mode: {journal}\n"
        f"Delete protection: active while this application is open\n\n"
        f"Recovery-copy folder: {catalog.backup_directory}\nCopies: {len(backups):,}\nLatest: {latest}\n\n"
        "The live database must remain writable, so it cannot be made read-only. Automatic consistent "
        "copies are created once per day. Administrators can still remove files after the app closes.",
        title="Catalog safety and recovery",
    ))
    action = Prompt.ask("B create a recovery copy now; S return", choices=["B", "S"], default="S").upper()
    if action == "B":
        destination = catalog.create_backup("manual")
        console.print(f"Catalog recovery copy created: {destination}")


def choose_restore_categories(catalog: Catalog, backup_id: str, deleted_only: bool) -> set[str] | None:
    cleanup_filter = "AND cleanup_status='deleted'" if deleted_only else ""
    rows = catalog.connection.execute(
        f"""SELECT category,COUNT(1) count,COALESCE(SUM(size),0) bytes
        FROM backup_items WHERE job_id=? AND status IN ('copied','skipped_duplicate')
        AND sha256 IS NOT NULL AND COALESCE(target_path,duplicate_of) IS NOT NULL
        {cleanup_filter} GROUP BY category ORDER BY bytes DESC""", (backup_id,)
    ).fetchall()
    if not rows:
        return set()
    table = Table(title="Restore categories available in this backup")
    for heading in ("#", "Category", "Files", "Size"):
        table.add_column(heading)
    available: list[str] = []
    for number, row in enumerate(rows, 1):
        category = str(row["category"])
        available.append(category)
        table.add_row(
            str(number), CATEGORY_LABELS.get(category, category),
            f"{int(row['count']):,}", human_bytes(int(row["bytes"])),
        )
    console.print(table)
    console.print(
        "Enter A for all, category numbers (example: 1 2), or shortcuts: "
        "P photos, V videos, AU audio, D documents, AR archives, O other."
    )
    while True:
        answer = Prompt.ask("Categories to restore", default="A")
        try:
            selected = apply_category_selection(available, answer)
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            continue
        return selected


def choose_restore_target(backup: object, devices: list[Device]) -> Device:
    if backup["source_kind"] == "mtp":
        try:
            original_id = str(json.loads(str(backup["source_ref"]))["device_id"])
        except (ValueError, KeyError, TypeError):
            original_id = ""
        original = next((device for device in devices if device.id == original_id), None)
        if original and Confirm.ask(
            f"Restore to the original device: {original.label}?", default=True,
        ):
            return original
        console.print(
            "Choose a connected phone/tablet. Original folder paths will be recreated on it."
        )
        return choose_device(devices, "Restore device number", kind="mtp")

    filesystem = [device for device in devices if device.is_filesystem]
    if not filesystem:
        raise RuntimeError("No filesystem device is connected for this restore.")
    try:
        roots = [Path(value) for value in json.loads(str(backup["source_ref"]))]
    except (ValueError, TypeError):
        roots = []
    original = next(
        (device for device in filesystem if any(
            Path(device.root).resolve().anchor.lower() == root.resolve().anchor.lower()
            for root in roots
        )),
        None,
    )
    if original and Confirm.ask(
        f"Restore to the original filesystem device: {original.label}?", default=True,
    ):
        return original
    console.print("Choose a filesystem device; original paths below its drive root will be recreated.")
    return choose_device(filesystem, "Restore device number", filesystem_only=True)


def run_restore_job(manager: RestoreManager, catalog: Catalog, restore_id: str) -> None:
    row = catalog.connection.execute(
        "SELECT * FROM restore_jobs WHERE id=?", (restore_id,)
    ).fetchone()
    if not row:
        raise KeyError(restore_id)
    is_mtp = row["target_kind"] == "mtp"
    columns = [
        TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(),
        TextColumn("{task.fields[details]}"), TimeRemainingColumn(),
    ]
    with Progress(*columns, console=console) as progress:
        task = progress.add_task(
            "Restoring", total=int(row["total_bytes"]), completed=int(row["completed_bytes"]),
            details="starting; MTP restores include SHA-256 read-back" if is_mtp else "starting",
        )

        def update(done: int, total: int, byte_count: int, name: str) -> None:
            progress.update(
                task, completed=min(int(row["total_bytes"]), byte_count),
                details=f"{done:,}/{total:,} this run | {name[:45]}",
            )

        result = manager.run_job(restore_id, update)
    console.print(Panel(
        f"Restore ID: {result.restore_id}\nStatus: {result.status}\n"
        f"Restored: {result.restored:,}\nAlready identical: {result.skipped:,}\n"
        f"Conflicts kept without overwrite: {result.conflicts:,}\nFailed/pending: {result.failed:,}\n"
        f"Verified data: {human_bytes(result.completed_bytes)}",
        title="Restore result",
    ))


def start_restore(catalog: Catalog, paths: AppPaths, devices: list[Device]) -> None:
    rows = catalog.connection.execute(
        """SELECT j.*,
        SUM(i.status IN ('copied','skipped_duplicate') AND i.sha256 IS NOT NULL
            AND COALESCE(i.target_path,i.duplicate_of) IS NOT NULL) restorable,
        SUM(i.status IN ('copied','skipped_duplicate') AND i.cleanup_status='deleted'
            AND i.sha256 IS NOT NULL AND COALESCE(i.target_path,i.duplicate_of) IS NOT NULL) deleted,
        COALESCE(SUM(CASE WHEN i.status IN ('copied','skipped_duplicate')
            AND i.cleanup_status='deleted' THEN i.size ELSE 0 END),0) deleted_bytes
        FROM backup_jobs j JOIN backup_items i ON i.job_id=j.id
        WHERE j.status IN ('completed','completed_with_errors')
        GROUP BY j.id HAVING restorable>0 ORDER BY j.created_at DESC"""
    ).fetchall()
    if not rows:
        console.print("No completed backups with verified restore files were found.")
        return
    table = Table(title="Backups available for restore")
    for heading in ("#", "Backup ID", "Source", "Restorable", "Deleted from source", "Deleted data"):
        table.add_column(heading)
    for number, row in enumerate(rows, 1):
        table.add_row(
            str(number), str(row["id"]), str(row["source_kind"]),
            f"{int(row['restorable'] or 0):,}", f"{int(row['deleted'] or 0):,}",
            human_bytes(int(row["deleted_bytes"] or 0)),
        )
    console.print(table)
    answer = Prompt.ask("Backup number to restore, or X", default="X").strip().upper()
    if answer == "X":
        return
    if not answer.isdigit() or not 1 <= int(answer) <= len(rows):
        raise ValueError("Choose a displayed backup number or X.")
    backup = rows[int(answer) - 1]
    console.print(
        "1. Restore only files previously deleted from the source [recommended]\n"
        "2. Check/restore all verified files in this backup\n"
        "X. Cancel"
    )
    scope = Prompt.ask("Restore scope", choices=["1", "2", "X"], default="1").upper()
    if scope == "X":
        return
    deleted_only = scope == "1"
    if deleted_only and not int(backup["deleted"] or 0):
        console.print("This backup has no files recorded as deleted from the source.")
        return
    categories = choose_restore_categories(catalog, str(backup["id"]), deleted_only)
    if not categories:
        console.print("Restore cancelled or no category was selected.")
        return
    target = choose_restore_target(backup, devices)
    manager = RestoreManager(catalog, paths.staging)
    restore_id = manager.create_job(
        str(backup["id"]), target, categories, deleted_only=deleted_only,
    )
    job = catalog.connection.execute("SELECT * FROM restore_jobs WHERE id=?", (restore_id,)).fetchone()
    preview = manager.preview(restore_id)
    table = Table(title="Restore preview — original paths")
    for heading in ("#", "Category", "Backup proof", "Restore destination"):
        table.add_column(heading)
    for number, item in enumerate(preview, 1):
        table.add_row(
            str(number), CATEGORY_LABELS.get(item["category"], item["category"]),
            str(item["proof_path"]), str(item["restore_path"]),
        )
    console.print(table)
    if int(job["total_files"]) > len(preview):
        console.print(f"...and {int(job['total_files']) - len(preview):,} more mappings.")
    rate_key = "mtp_restore_bytes" if target.kind == "mtp" else "restore_bytes"
    fallback_rate = 2.5 * 1024 * 1024 if target.kind == "mtp" else 40 * 1024 * 1024
    estimated_seconds = int(job["total_bytes"]) / catalog.estimate_rate(rate_key, fallback_rate)
    free_text = human_bytes(target.free_bytes)
    console.print(Panel(
        f"Restore ID: {restore_id}\nTarget: {target.label}\n"
        f"Files: {int(job['total_files']):,}\nData: {human_bytes(int(job['total_bytes']))}\n"
        f"Estimated duration: {human_duration(estimated_seconds)}\nTarget free: {free_text}\n"
        "Existing identical files are skipped. Different existing files are never overwritten.\n"
        "Phone/tablet restores are copied back and read again for SHA-256 verification.",
        title="Final restore plan",
    ))
    if target.free_bytes is not None and int(job["total_bytes"]) > target.free_bytes:
        with catalog.transaction() as connection:
            connection.execute(
                "UPDATE restore_jobs SET status='cancelled',updated_at=? WHERE id=?",
                (utc_now(), restore_id),
            )
        raise RuntimeError("Selected restore data exceeds the target's currently reported free space.")
    if not Confirm.ask("Start this resumable restore?", default=False):
        with catalog.transaction() as connection:
            connection.execute(
                "UPDATE restore_jobs SET status='cancelled',updated_at=? WHERE id=?",
                (utc_now(), restore_id),
            )
        console.print("Restore cancelled before copying. No destination files were changed.")
        return
    run_restore_job(manager, catalog, restore_id)


def manage_restore_jobs(catalog: Catalog, paths: AppPaths, *, startup: bool = False) -> None:
    manager = RestoreManager(catalog, paths.staging)
    jobs = manager.resumable_jobs()
    if not jobs:
        if not startup:
            console.print("No resumable restore jobs were found.")
        return
    table = Table(title="Resumable restore jobs found")
    for heading in ("#", "Restore ID", "Backup ID", "Target", "Status", "Remaining"):
        table.add_column(heading)
    for number, job in enumerate(jobs, 1):
        table.add_row(
            str(number), str(job["id"]), str(job["backup_job_id"]),
            str(job["target_kind"]), str(job["status"]), str(job["remaining_files"]),
        )
    console.print(table)
    answer = Prompt.ask("Restore number/R<number> to resume, or S to skip", default="S").strip().upper()
    if answer in {"S", "X", ""}:
        return
    if answer.startswith("R"):
        answer = answer[1:]
    if not answer.isdigit() or not 1 <= int(answer) <= len(jobs):
        raise ValueError("Choose a displayed restore number or S.")
    run_restore_job(manager, catalog, str(jobs[int(answer) - 1]["id"]))


def review_job(catalog: Catalog, job_id: str, details: bool = False) -> None:
    rows = catalog.connection.execute(
        "SELECT status,cleanup_status,COUNT(*) count,COALESCE(SUM(size),0) bytes FROM backup_items WHERE job_id=? GROUP BY status,cleanup_status",
        (job_id,),
    ).fetchall()
    table = Table(title=f"Backup review: {job_id}")
    for heading in ("Result", "Cleanup", "Files", "Space on source"):
        table.add_column(heading)
    for row in rows:
        table.add_row(row["status"], row["cleanup_status"], str(row["count"]), human_bytes(row["bytes"]))
    console.print(table)
    if details:
        items = catalog.connection.execute(
            """SELECT source_path,status,size,target_path,duplicate_of FROM backup_items
            WHERE job_id=? ORDER BY status,source_path LIMIT 200""", (job_id,)
        ).fetchall()
        detail_table = Table(title="Per-file details (first 200)")
        for heading in ("Status", "Size", "Source", "Target / duplicate proof"):
            detail_table.add_column(heading)
        for item in items:
            detail_table.add_row(item["status"], human_bytes(item["size"]), item["source_path"],
                                 item["target_path"] or item["duplicate_of"] or "")
        console.print(detail_table)


def show_duplicates(catalog: Catalog, volume_id: str) -> None:
    groups = catalog.connection.execute(
        """SELECT sha256,size,COUNT(*) count,size*(COUNT(*)-1) reclaimable
        FROM files WHERE volume_id=? AND sha256 IS NOT NULL GROUP BY sha256,size
        HAVING COUNT(*)>1 ORDER BY reclaimable DESC LIMIT 100""", (volume_id,)
    ).fetchall()
    table = Table(title="Exact duplicate groups (top 100)")
    for heading in ("SHA-256", "Copies", "Each", "Potential reclaim"):
        table.add_column(heading)
    for row in groups:
        table.add_row(str(row["sha256"])[:16] + "...", str(row["count"]), human_bytes(row["size"]),
                      human_bytes(row["reclaimable"]))
    console.print(table)
    console.print(f"Potential space across shown groups: {human_bytes(sum(int(row['reclaimable']) for row in groups))}")


def show_summary_table(title: str, rows: list[tuple[str, int, int]]) -> None:
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Folder / category")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    for number, (name, count, byte_count) in enumerate(rows, 1):
        table.add_row(str(number), name, f"{count:,}", human_bytes(byte_count))
    console.print(table)


def show_mtp_folder_choices(title: str, children: list[tuple[str, bool, int]]) -> None:
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Folder / file")
    table.add_column("Type")
    for number, (name, is_folder, _) in enumerate(children, 1):
        table.add_row(str(number), name, "Folder" if is_folder else "Root file")
    console.print(table)


def choose_mtp_inventory_folders(
    children: list[tuple[str, bool, int]],
) -> list[tuple[str, bool, int]] | None:
    console.print(
        "Type the folder numbers you want to [bold]include[/bold], separated by spaces "
        "(example: [bold]9 21[/bold]). Press Enter for all folders, or enter X to cancel."
    )
    while True:
        answer = Prompt.ask(
            "Folder numbers to include",
            default="A", show_default=False,
        )
        try:
            return apply_numbered_selection(children, answer)
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")


def show_mtp_inventory(title: str, groups: list[tuple[str, list[MtpEntry]]]) -> None:
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Folder / file")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Categories involved")
    for number, (name, group_entries) in enumerate(groups, 1):
        sources = mtp_sources(group_entries)
        category_rows = summarize(sources, "category")
        categories = ", ".join(
            f"{CATEGORY_LABELS.get(category, category)} ({count:,})"
            for category, count, _ in category_rows
        ) or "Empty"
        table.add_row(
            str(number), name, f"{len(group_entries):,}",
            human_bytes(sum(entry.size for entry in group_entries)), categories,
        )
    console.print(table)


def inventory_mtp_folders(
    device: Device,
    purpose: str,
) -> list[tuple[str, list[MtpEntry]]] | None:
    """Offer fast-first or confirmed detailed-all MTP folder discovery."""
    children = list_children(device.id)
    if not children:
        raise RuntimeError("No readable folders or files were found on the portable device.")
    show_mtp_folder_choices(f"Top-level folders/files on {device.label}", children)
    console.print(Panel(
        "1. [bold]Select folders first (recommended)[/bold]\n"
        "   Enter folder numbers now; only those folders are scanned. This is faster.\n\n"
        "2. [bold]Scan all folders first[/bold]\n"
        "   Calculate sizes and categories for every folder, then choose. This may take several minutes.\n\n"
        "X. Cancel and return to the main menu",
        title="Step 2 — Choose how much of the device to inspect",
    ))
    mode = Prompt.ask(
        "Choose 1, 2, or X",
        choices=["1", "2", "X"], default="1",
    ).upper()
    if mode == "X":
        return None

    def read_groups(selected_children: list[tuple[str, bool, int]]) -> list[tuple[str, list[MtpEntry]]]:
        paths = [name for name, _, _ in selected_children]
        with console.status(f"Starting read-only {purpose} inventory...") as status:
            def update(current: str, count: int, byte_count: int) -> None:
                status.update(
                    f"Reading {current}: {count:,} files found / {human_bytes(byte_count)}..."
                )

            return collect_entry_groups(device.id, paths, update)

    if mode == "2":
        console.print("Scanning all folders now. This is read-only; no files will be changed.")
        groups = read_groups(children)
        show_mtp_inventory(f"Detailed all-folder inventory on {device.label}", groups)
        chosen = choose_mtp_inventory_folders(children)
    else:
        chosen = choose_mtp_inventory_folders(children)
        if chosen is None:
            return None
        if not chosen:
            raise RuntimeError("No portable-device folders were selected.")
        console.print("Scanning only the selected folders. This is read-only; no files will be changed.")
        groups = read_groups(chosen)
        show_mtp_inventory(f"Detailed selected-folder inventory on {device.label}", groups)

    if chosen is None:
        return None
    chosen_names = {name for name, _, _ in chosen}
    selected_groups = [group for group in groups if group[0] in chosen_names]
    if not selected_groups:
        raise RuntimeError("No inventoried folders were selected.")
    show_mtp_inventory("Folders included after review", selected_groups)
    return selected_groups


def choose_categories(entries: list[SourceFile]) -> set[str]:
    rows = summarize(entries, "category")
    labelled = [(CATEGORY_LABELS.get(name, name), count, byte_count) for name, count, byte_count in rows]
    show_summary_table("Available file categories - choose what to back up", labelled)
    available = [name for name, _, _ in rows]
    while True:
        answer = Prompt.ask(
            "Categories: P photos, V videos, AU audio, D documents, AR archives, O other; "
            "combine shortcuts/numbers; A all; X cancel",
            default=None,
        )
        try:
            selected = apply_category_selection(available, answer)
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            continue
        if selected is None:
            return set()
        chosen_rows = [
            labelled[index] for index, row in enumerate(rows) if row[0] in selected
        ]
        show_summary_table("Categories included in backup", chosen_rows)
        return selected


def choose_exclusions(entries: list[SourceFile], attribute: str, title: str,
                      page_size: int = 25) -> set[str]:
    rows = summarize(entries, attribute)
    excluded: set[str] = set()
    page = 0
    while True:
        remaining = [row for row in rows if row[0] not in excluded]
        if not remaining:
            console.print(f"All {len(rows):,} listed folders/categories are excluded.")
            break
        pages = max(1, (len(remaining) + page_size - 1) // page_size)
        page = min(page, pages - 1)
        visible = remaining[page * page_size:(page + 1) * page_size]
        show_summary_table(f"{title} - page {page + 1}/{pages}", visible)
        answer = Prompt.ask(
            f"Numbers to exclude; N/P pages; D done ({len(excluded)} excluded)",
            default="D",
        ).strip().upper()
        if answer == "D":
            break
        if answer == "N":
            if page >= pages - 1:
                console.print("[yellow]Already on the last page.[/yellow]")
            else:
                page += 1
            continue
        if answer == "P":
            if page == 0:
                console.print("[yellow]Already on the first page.[/yellow]")
            else:
                page -= 1
            continue
        selected = parse_number_selection(answer, len(visible))
        for number in selected:
            excluded.add(visible[number - 1][0])
        if selected:
            console.print(f"Excluded {len(selected)} selection(s); {len(excluded)} total.")
        else:
            console.print("[yellow]No valid row numbers were entered.[/yellow]")
    console.print(
        f"Exclusion review complete: {len(excluded):,} excluded; "
        f"{len(rows) - len(excluded):,} kept."
    )
    return excluded


def choose_inclusions(entries: list[SourceFile], attribute: str, title: str,
                      page_size: int = 25) -> set[str]:
    rows = summarize(entries, attribute)
    selected: set[str] = set()
    page = 0
    while True:
        remaining = [row for row in rows if row[0] not in selected]
        if not remaining:
            break
        pages = max(1, (len(remaining) + page_size - 1) // page_size)
        page = min(page, pages - 1)
        visible = remaining[page * page_size:(page + 1) * page_size]
        show_summary_table(f"{title} - select folders to include - page {page + 1}/{pages}", visible)
        answer = Prompt.ask(
            f"Numbers to INCLUDE; N/P pages; A include all; D done ({len(selected)} selected)",
            default="D", show_default=False,
        ).strip().upper()
        if answer == "D":
            if selected:
                break
            console.print("[yellow]Select at least one folder, or enter A to include all.[/yellow]")
            continue
        if answer == "A":
            selected = {row[0] for row in rows}
            break
        if answer == "N":
            if page >= pages - 1:
                console.print("[yellow]Already on the last page.[/yellow]")
            else:
                page += 1
            continue
        if answer == "P":
            if page == 0:
                console.print("[yellow]Already on the first page.[/yellow]")
            else:
                page -= 1
            continue
        chosen = parse_number_selection(answer, len(visible))
        for number in chosen:
            selected.add(visible[number - 1][0])
        if chosen:
            console.print(f"Selected {len(chosen)} folder(s); {len(selected)} total.")
        else:
            console.print("[yellow]No valid row numbers were entered.[/yellow]")
    console.print(
        f"Folder selection complete: {len(selected):,} included; "
        f"{len(rows) - len(selected):,} omitted."
    )
    selected_rows = [row for row in rows if row[0] in selected]
    show_summary_table("Included folders (first 50)", selected_rows[:50])
    return selected


def choose_folder_scope(entries: list[SourceFile], attribute: str, title: str) -> set[str]:
    """Return excluded names while making include-only selection the primary flow."""
    rows = summarize(entries, attribute)
    if not rows:
        return set()
    console.print(
        "1. Select only folders to include [recommended]\n"
        "2. Keep all and exclude some folders\n"
        "3. Keep all folders"
    )
    mode = Prompt.ask("Folder selection mode", choices=["1", "2", "3"], default="1")
    if mode == "3":
        return set()
    if mode == "2":
        return choose_exclusions(entries, attribute, title)
    included = choose_inclusions(entries, attribute, title)
    return {row[0] for row in rows if row[0] not in included}


def source_category_routes(
    entries: list[SourceFile],
) -> list[tuple[tuple[str, str], int, int]]:
    totals: dict[tuple[str, str], tuple[int, int]] = {}
    for entry in entries:
        key = (entry.parent_folder, entry.category)
        count, byte_count = totals.get(key, (0, 0))
        totals[key] = (count + 1, byte_count + entry.size)
    return sorted(
        ((key, count, byte_count) for key, (count, byte_count) in totals.items()),
        key=lambda row: (-row[2], row[0][0].lower(), row[0][1]),
    )


def show_destination_routes(
    title: str,
    rows: list[tuple[tuple[str, str], int, int]],
    routes: dict[tuple[str, str], str],
    vault_root: Path,
) -> None:
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Source folder")
    table.add_column("Category")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Target folder")
    for number, (key, count, byte_count) in enumerate(rows, 1):
        source_folder, category = key
        table.add_row(
            str(number), source_folder, CATEGORY_LABELS.get(category, category),
            f"{count:,}", human_bytes(byte_count), str(vault_root / Path(routes[key])),
        )
    console.print(table)


def target_relative_to_vault(vault_root: Path, value: str) -> str:
    requested = Path(value.strip())
    candidate = requested if requested.is_absolute() else vault_root / requested
    vault = vault_root.resolve()
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(vault)
    except ValueError as exc:
        raise ValueError(f"Target folder must stay inside the selected vault: {vault}") from exc
    if relative == Path("."):
        raise ValueError("Choose a folder inside the vault, not the vault root itself.")
    return relative.as_posix()


def configure_destination_routes(
    entries: list[SourceFile],
    vault_root: Path,
) -> dict[tuple[str, str], str] | None:
    rows = source_category_routes(entries)
    routes = {
        key: CATEGORY_DESTINATIONS[key[1]]
        for key, _, _ in rows
    }
    while True:
        show_destination_routes("Source folder to target-folder routing", rows, routes, vault_root)
        answer = Prompt.ask(
            "A accept routes; E <number> edit target; R reset defaults; X cancel",
            default="A",
        ).strip().upper()
        if answer == "A":
            show_destination_routes("FINAL backup routing", rows, routes, vault_root)
            return routes
        if answer == "X":
            return None
        if answer == "R":
            routes = {key: CATEGORY_DESTINATIONS[key[1]] for key, _, _ in rows}
            console.print("All target folders reset to category defaults.")
            continue
        compact = answer.replace(" ", "")
        if compact.startswith("E") and compact[1:].isdigit():
            number = int(compact[1:])
            if 1 <= number <= len(rows):
                key = rows[number - 1][0]
                current = str(vault_root / Path(routes[key]))
                value = Prompt.ask("New target folder inside the vault", default=current)
                try:
                    routes[key] = target_relative_to_vault(vault_root, value)
                    console.print(
                        f"Route {number} updated: {key[0]} / "
                        f"{CATEGORY_LABELS.get(key[1], key[1])} -> {vault_root / Path(routes[key])}"
                    )
                except ValueError as exc:
                    console.print(f"[yellow]{exc}[/yellow]")
                continue
        console.print("[yellow]Enter A, E followed by a displayed number, R, or X.[/yellow]")


def preview_selection(entries: list[SourceFile], vault_root: Path) -> None:
    table = Table(title="Final file preview (first 20 files)")
    table.add_column("Category")
    table.add_column("Size", justify="right")
    table.add_column("Destination")
    for entry in entries[:20]:
        table.add_row(CATEGORY_LABELS.get(entry.category, entry.category), human_bytes(entry.size),
                      str(vault_root / Path(entry.relative_path)))
    console.print(table)
    console.print(
        f"Selected: {len(entries):,} files / {human_bytes(sum(entry.size for entry in entries))}. "
        f"Vault root: {vault_root}"
    )


def target_device_for_path(devices: list[Device], path: Path) -> Device:
    resolved = path.resolve()
    candidates = [device for device in devices if device.is_filesystem]
    for device in candidates:
        try:
            resolved.relative_to(Path(device.root).resolve())
            return device
        except ValueError:
            continue
    raise ValueError(f"No connected filesystem device contains {path}.")


def guided_backup(catalog: Catalog, paths: AppPaths, devices: list[Device]) -> str | None:
    source_device = choose_device(devices, "Source device number")
    default_vault = default_vault_root(paths.root)
    vault_text = Prompt.ask("Vault root (category folders will be created here)", default=str(default_vault))
    vault_root = Path(vault_text)
    target_device = target_device_for_path(devices, vault_root)
    selected_top_paths: list[str] = []
    live_mtp_entries = []

    if source_device.kind == "mtp":
        selected_groups = inventory_mtp_folders(source_device, "backup")
        if selected_groups is None:
            console.print("Backup cancelled before device inventory selection completed.")
            return None
        selected_top_paths = [name for name, _ in selected_groups]
        live_mtp_entries = [entry for _, group_entries in selected_groups for entry in group_entries]
        entries = mtp_sources(live_mtp_entries)
        excluded_major: set[str] = set()
        source_paths: list[Path] = []
    else:
        if Confirm.ask(f"Use the entire {source_device.root} device as the source?", default=True):
            source_paths = [Path(source_device.root)]
        else:
            values = Prompt.ask("Source folder/file paths separated by semicolons")
            source_paths = [Path(value.strip()) for value in values.split(";") if value.strip()]
        console.print("Reading source metadata to calculate categories, folders, size and ETA...")
        entries = collect_filesystem_sources(source_paths)
        excluded_major = choose_folder_scope(entries, "major_folder", "Major folders")

    if not entries:
        raise RuntimeError("No readable source files were found.")
    categories = choose_categories(entries)
    if not categories:
        console.print("Backup cancelled at category selection.")
        return None
    category_filtered = filter_sources(entries, categories, excluded_major, set())
    selected_category_labels = ", ".join(
        CATEGORY_LABELS.get(category, category) for category in sorted(categories)
    )
    excluded_folders = choose_folder_scope(
        category_filtered, "parent_folder",
        f"Source folders containing: {selected_category_labels} (sorted by selected data size)",
    )
    selected = filter_sources(entries, categories, excluded_major, excluded_folders)
    if not selected:
        raise RuntimeError("All files were excluded from the backup.")
    destination_routes = configure_destination_routes(selected, vault_root)
    if destination_routes is None:
        console.print("Backup cancelled during target-folder routing.")
        return None
    planned = categorize_destinations(
        selected, source_device.label, destination_folders=destination_routes,
    )
    preview_selection(planned, vault_root)
    estimate = estimate_selected(catalog, planned, source_device.kind)
    free = shutil.disk_usage(target_device.root).free
    console.print(
        f"Estimated duration: {human_duration(estimate.seconds)}; target free: {human_bytes(free)}."
    )
    if estimate.bytes > free:
        raise RuntimeError("The selected files exceed the target device's free space.")
    if not Confirm.ask("Start this resumable, SHA-256 verified backup?", default=True):
        return None

    selection = {
        "categories": sorted(categories),
        "excluded_major_folders": sorted(excluded_major),
        "excluded_folders": sorted(excluded_folders),
        "destination_routes": [
            {
                "source_folder": source_folder,
                "category": category,
                "target_folder": target_folder,
            }
            for (source_folder, category), target_folder in destination_routes.items()
        ],
        "vault_root": str(vault_root),
    }
    manager = BackupManager(catalog, paths.staging)
    if source_device.kind == "mtp":
        selected_paths = {entry.source_path for entry in selected}
        selected_live = [entry for entry in live_mtp_entries if entry.logical_path in selected_paths]
        job_id = manager.create_mtp_job(
            source_device.id, selected_top_paths, selected_live, target_device, vault_root,
            selection=selection, included_files=planned,
        )
    else:
        job_id = manager.create_job(
            source_paths, target_device, vault_root, included_entries=planned, selection=selection,
        )
    console.print(f"Backup ID: [bold]{job_id}[/bold]")
    run_backup_job(manager, catalog, job_id)
    status = catalog.connection.execute("SELECT status FROM backup_jobs WHERE id=?", (job_id,)).fetchone()
    if status and status["status"] == "completed" and Confirm.ask(
        "Review verified backed-up source files to free device space now?", default=True
    ):
        session_id = CleanupManager(catalog).create_from_job(job_id)
        run_cleanup_session(catalog, paths, devices, session_id)
    return job_id


def show_storage_after_delete(devices: list[Device], session: object, session_freed: int,
                              source_path: str = "") -> None:
    if session["source_kind"] == "mtp":
        refreshed = next((device for device in connected_devices() if device.id == session["source_device_id"]), None)
        if refreshed and refreshed.total_bytes is not None and refreshed.free_bytes is not None:
            console.print(
                f"Device free: {human_bytes(refreshed.free_bytes)} / {human_bytes(refreshed.total_bytes)}; "
                f"session reclaimed: {human_bytes(session_freed)}"
            )
        else:
            console.print(f"Device storage is refreshing; session reclaimed: {human_bytes(session_freed)}")
        return
    root = Path(source_path or str(session["source_ref"]))
    if not root.drive:
        root = Path(next(
            (str(device.root) for device in devices if device.is_filesystem),
            str(default_vault_root()),
        ))
    usage = shutil.disk_usage(root.anchor or root)
    console.print(
        f"Device free: {human_bytes(usage.free)} / {human_bytes(usage.total)}; "
        f"session reclaimed: {human_bytes(session_freed)}"
    )


def back_up_cleanup_item(catalog: Catalog, paths: AppPaths, devices: list[Device],
                         session_id: str, item: object) -> None:
    manager = CleanupManager(catalog)
    session = manager.session(session_id)
    vault_root = Path(Prompt.ask("Vault root", default=str(default_vault_root(paths.root))))
    target = target_device_for_path(devices, vault_root)
    backup_manager = BackupManager(catalog, paths.staging)
    if session["source_kind"] == "mtp":
        entries = collect_entries(str(session["source_device_id"]), [str(item["source_path"])])
        source_files = mtp_sources(entries)
        routes = configure_destination_routes(source_files, vault_root)
        if routes is None:
            raise RuntimeError("Cleanup-item backup cancelled during target routing.")
        planned = categorize_destinations(
            source_files, "Cleanup source", destination_folders=routes,
        )
        job_id = backup_manager.create_mtp_job(
            str(session["source_device_id"]), [str(item["source_path"])], entries,
            target, vault_root, included_files=planned,
        )
    else:
        source = Path(str(item["source_path"]))
        source_files = collect_filesystem_sources([source])
        routes = configure_destination_routes(source_files, vault_root)
        if routes is None:
            raise RuntimeError("Cleanup-item backup cancelled during target routing.")
        planned = categorize_destinations(
            source_files, source.drive or "Device", destination_folders=routes,
        )
        job_id = backup_manager.create_job([source], target, vault_root, included_entries=planned)
    console.print(f"Backup ID: {job_id}")
    run_backup_job(backup_manager, catalog, job_id)
    manager.attach_latest_proof(session_id, str(item["source_path"]))


def bulk_delete_verified_cleanup(
    manager: CleanupManager,
    devices: list[Device],
    session: object,
    session_id: str,
    pending: list[object],
) -> None:
    eligible = [item for item in pending if item["proof_path"] and item["sha256"]]
    total_bytes = sum(int(item["size"]) for item in eligible)
    console.print(Panel(
        f"Files selected: {len(eligible):,}\n"
        f"Potential source space: {human_bytes(total_bytes)}\n\n"
        "Backup proofs and source details are rechecked before deletion. For portable devices, "
        "verified files are sent to Windows in small batches for much better performance. "
        "Changed, missing, or unverifiable files are kept.\n\n"
        "Progress is saved after every successful deletion, so this operation can be resumed.",
        title="Bulk delete verified source files",
    ))
    if not eligible:
        console.print("No pending items have usable backup proof.")
        manager.set_session_status(session_id, "paused")
        return
    if session["source_kind"] == "mtp" and not any(
        device.id == session["source_device_id"] for device in connected_devices()
    ):
        raise RuntimeError("The source device is not connected or unlocked. No files were deleted.")
    confirmation = Prompt.ask(
        f"Type DELETE ALL to process {len(eligible):,} verified source files",
        default="", show_default=False,
    ).strip().upper()
    if confirmation != "DELETE ALL":
        manager.set_session_status(session_id, "paused")
        console.print("Bulk deletion cancelled. No files were changed.")
        return

    deleted = 0
    failed = 0
    reclaimed = 0
    columns = [
        TextColumn("[progress.description]{task.description}"), BarColumn(),
        TaskProgressColumn(), TextColumn("{task.fields[details]}"), TimeRemainingColumn(),
    ]
    try:
        with Progress(*columns, console=console) as progress:
            task = progress.add_task("Verified cleanup", total=len(eligible), details="starting")
            if session["source_kind"] == "mtp":
                batch_size = 25
                proof_cache: dict[tuple[str, int, int], str] = {}
                for start in range(0, len(eligible), batch_size):
                    batch = eligible[start:start + batch_size]
                    paths = [str(item["source_path"]) for item in batch]
                    batch_deleted, errors, batch_reclaimed = manager.delete_proven_mtp_batch(
                        session_id, paths, proof_cache,
                    )
                    deleted += len(batch_deleted)
                    failed += len(errors)
                    reclaimed += batch_reclaimed
                    for source_path, error in errors.items():
                        manager.note_item_error(session_id, source_path, error)
                    progress.update(
                        task, completed=min(start + len(batch), len(eligible)),
                        details=(
                            f"{deleted:,} deleted | {failed:,} kept | "
                            f"{human_bytes(reclaimed)} | batch {start // batch_size + 1}"
                        ),
                    )
            else:
                for position, item in enumerate(eligible, 1):
                    try:
                        reclaimed += manager.delete_proven_item(
                            session_id, str(item["source_path"]),
                        )
                        deleted += 1
                    except Exception as exc:
                        failed += 1
                        manager.note_item_error(session_id, str(item["source_path"]), str(exc))
                    progress.update(
                        task, completed=position,
                        details=(
                            f"{deleted:,} deleted | {failed:,} kept | "
                            f"{human_bytes(reclaimed)} | {item['source_name']}"
                        ),
                    )
    except KeyboardInterrupt:
        manager.set_session_status(session_id, "paused")
        console.print("\nCleanup paused safely. Completed deletions were saved; remaining files were kept.")
        return

    remaining = manager.pending_items(session_id)
    manager.set_session_status(session_id, "paused" if remaining else "completed")
    console.print(Panel(
        f"Deleted: {deleted:,}\nKept because verification failed: {failed:,}\n"
        f"Reclaimed: {human_bytes(reclaimed)}\nRemaining for later review: {len(remaining):,}",
        title="Bulk cleanup result",
    ))
    if deleted:
        show_storage_after_delete(devices, session, reclaimed)


def run_cleanup_session(catalog: Catalog, paths: AppPaths, devices: list[Device], session_id: str) -> None:
    manager = CleanupManager(catalog)
    session = manager.session(session_id)
    manager.set_session_status(session_id, "running")
    pending = manager.pending_items(session_id)
    if not pending:
        manager.set_session_status(session_id, "completed")
        console.print("This cleanup session has no pending files.")
        return
    console.print(Panel(
        f"Session: {session_id}\nQueue: {session['queue_type']}\nPending: {len(pending):,}\n"
        "Backed-up files can be processed in bulk or reviewed individually.\n"
        "Delete is enabled only after an immediately verified backup or exact duplicate proof.",
        title="Verified cleanup",
    ))
    if session["queue_type"] == "backed_up":
        console.print(
            "1. Bulk delete all files that still pass verification\n"
            "2. Review files one by one (largest first)\n"
            "X. Save and exit"
        )
        mode = Prompt.ask("Cleanup mode", choices=["1", "2", "X"], default="1").upper()
        if mode == "X":
            manager.set_session_status(session_id, "paused")
            return
        if mode == "1":
            bulk_delete_verified_cleanup(manager, devices, session, session_id, pending)
            return
    session_freed = 0

    for position, item in enumerate(pending, 1):
        console.print(Panel(
            f"{position}/{len(pending)}\nName: {item['source_name']}\nPath: {item['source_path']}\n"
            f"Category: {CATEGORY_LABELS.get(item['category'], item['category'])}\n"
            f"Size: {human_bytes(item['size'])}\nProof: {item['proof_path'] or 'Not available'}",
            title="Cleanup candidate",
        ))
        while True:
            proof_available = bool(item["proof_path"] and item["sha256"])
            actions = (
                "B backup only, BD backup + delete, O open, Enter skip, "
                "I ignore folder, X save/exit"
            )
            if proof_available:
                actions = "D delete already-verified, " + actions
            action = Prompt.ask(actions, default="S", show_default=False).strip().upper()
            if action == "X":
                manager.set_session_status(session_id, "paused")
                return
            if action == "O":
                try:
                    if session["source_kind"] == "mtp":
                        open_entry(str(session["source_device_id"]), str(item["source_path"]))
                        console.print("Open request sent to Windows. Return here after reviewing the file.")
                    else:
                        subprocess.Popen(["explorer.exe", "/select,", str(item["source_path"])])
                except Exception as exc:
                    console.print(f"[red]Windows could not open this item: {exc}[/red]")
                    console.print(f"Device path: {item['source_path']}")
                continue
            if action == "I":
                count = manager.ignore_folder(session_id, str(item["source_path"]))
                console.print(f"Ignored {count} pending item(s) from this folder.")
                break
            delete_confirmed = False
            if action in {"B", "BD"}:
                try:
                    back_up_cleanup_item(catalog, paths, devices, session_id, item)
                    console.print("Backup completed and SHA-256 proof attached.")
                    if action == "B":
                        manager.mark_item(
                            session_id, str(item["source_path"]), "skipped", "backup_keep",
                        )
                        break
                    if not Confirm.ask(
                        "Backup verified. Permanently delete the source file now?", default=False,
                    ):
                        manager.mark_item(
                            session_id, str(item["source_path"]), "skipped", "backup_keep",
                        )
                        break
                    delete_confirmed = True
                except Exception as exc:
                    console.print(f"[red]Backup failed; source kept: {exc}[/red]")
                    continue
            if action in {"D", "BD"}:
                if action == "D" and not proof_available:
                    console.print("[yellow]No verified backup proof is available.[/yellow]")
                    continue
                if not delete_confirmed and not Confirm.ask(
                    "Permanently delete this verified source file?", default=False,
                ):
                    continue
                try:
                    session_freed += manager.delete_proven_item(
                        session_id, str(item["source_path"]),
                    )
                    console.print(f"Deleted and reclaimed {human_bytes(item['size'])}.")
                    show_storage_after_delete(
                        devices, session, session_freed, str(item["source_path"]),
                    )
                    break
                except Exception as exc:
                    console.print(f"[red]Deletion refused; source kept: {exc}[/red]")
                    continue
            if action in {"", "S"}:
                manager.mark_item(session_id, str(item["source_path"]), "skipped", "skip")
                break
            console.print(
                "[yellow]Enter B, BD, O, D when offered, I, X, or press Enter to skip.[/yellow]"
            )
    manager.set_session_status(
        session_id, "paused" if manager.pending_items(session_id) else "completed"
    )


def start_cleanup(catalog: Catalog, paths: AppPaths, devices: list[Device]) -> None:
    console.print(
        "1. Review and remove files already verified by a completed BACKUP [recommended]\n"
        "2. Discover large files or exact duplicates on a device\n"
        "X. Return to main menu"
    )
    strategy = Prompt.ask("Cleanup strategy", choices=["1", "2", "X"], default="1").upper()
    if strategy == "X":
        return
    manager = CleanupManager(catalog)
    if strategy == "1":
        jobs = catalog.connection.execute(
            """SELECT j.id,j.source_kind,j.status,j.created_at,
            COUNT(i.source_path) eligible_files,COALESCE(SUM(i.size),0) eligible_bytes
            FROM backup_jobs j JOIN backup_items i ON i.job_id=j.id
            WHERE j.status IN ('completed','completed_with_errors')
            AND i.status IN ('copied','skipped_duplicate')
            AND i.cleanup_status<>'deleted'
            AND NOT EXISTS (
                SELECT 1 FROM cleanup_sessions s
                WHERE s.source_ref='backup_job:' || j.id
                AND s.status IN ('planned','running','paused')
            )
            GROUP BY j.id HAVING eligible_files>0
            ORDER BY j.created_at DESC"""
        ).fetchall()
        if not jobs:
            console.print(
                "No completed backup has unqueued verified files. Resume an existing backed-up "
                "cleanup session with menu option 8, or complete a backup first."
            )
            return
        table = Table(title="Completed backups eligible for verified source cleanup")
        for heading in ("#", "Backup ID", "Source", "Status", "Verified files", "Source space"):
            table.add_column(heading)
        for number, job in enumerate(jobs, 1):
            table.add_row(
                str(number), job["id"], job["source_kind"], job["status"],
                f"{job['eligible_files']:,}", human_bytes(job["eligible_bytes"]),
            )
        console.print(table)
        answer = Prompt.ask("Backup number to review, or X to return").strip().upper()
        if answer == "X":
            return
        if not answer.isdigit() or not 1 <= int(answer) <= len(jobs):
            raise ValueError("Choose a displayed completed-backup number or X.")
        job = jobs[int(answer) - 1]
        console.print(Panel(
            f"Backup: {job['id']}\nVerified source files: {job['eligible_files']:,}\n"
            f"Potential source space: {human_bytes(job['eligible_bytes'])}\n"
            "Files will be shown largest first. Nothing is deleted automatically.",
            title="Create verified cleanup review",
        ))
        if not Confirm.ask("Create and open this cleanup review session?", default=True):
            return
        session_id = manager.create_from_job(str(job["id"]))
        console.print(f"Cleanup session ID: {session_id}")
        run_cleanup_session(catalog, paths, devices, session_id)
        return

    show_devices(devices)
    while True:
        answer = Prompt.ask("Device number to clean, or X to return").strip().upper()
        if answer == "X":
            return
        if answer.isdigit() and 1 <= int(answer) <= len(devices):
            device = devices[int(answer) - 1]
            break
        console.print("[yellow]Enter a listed device number or X.[/yellow]")

    if device.kind == "mtp":
        console.print(Panel(
            "Phone cleanup first creates a read-only largest-file queue. A source file can be "
            "deleted only after this app backs it up and verifies its SHA-256 on the target disk. "
            "Exact-duplicate discovery is deferred until backup because MTP files must be transferred "
            "before they can be hashed.",
            title="Safe portable-device cleanup",
        ))
        groups = inventory_mtp_folders(device, "cleanup")
        if groups is None:
            return
        inventory_entries = mtp_sources(entry for _, group in groups for entry in group)
        categories = choose_categories(inventory_entries)
        if not categories:
            console.print("Cleanup cancelled at category selection.")
            return
        category_entries = filter_sources(inventory_entries, categories, set(), set())
        category_labels = ", ".join(
            CATEGORY_LABELS.get(category, category) for category in sorted(categories)
        )
        excluded_folders = choose_folder_scope(
            category_entries, "parent_folder",
            f"Cleanup folders containing: {category_labels} (sorted by selected data size)",
        )
        entries = filter_sources(inventory_entries, categories, set(), excluded_folders)
        if not entries:
            raise RuntimeError("No cleanup files remain after category and folder selection.")
        console.print(
            f"Cleanup scope: {len(entries):,} files / "
            f"{human_bytes(sum(entry.size for entry in entries))}."
        )
        threshold_mb = IntPrompt.ask("Minimum file size in MB", default=100)
        session_id = manager.create_largest_mtp(
            device.id, entries, threshold_mb * 1024**2, 200,
        )
        console.print(f"Cleanup session ID: {session_id}")
        run_cleanup_session(catalog, paths, devices, session_id)
        return

    indexed = catalog.connection.execute(
        "SELECT COUNT(*) FROM files WHERE volume_id=?", (device.id,)
    ).fetchone()[0]
    if not indexed:
        raise RuntimeError("This device is not indexed. Run Update index first.")
    console.print("1. Largest files (100 MB+, top 200)\n2. Exact duplicate copies\nX. Back")
    choice = Prompt.ask("Cleanup queue", default="1").upper()
    if choice == "X":
        return
    if choice == "1":
        threshold_mb = IntPrompt.ask("Minimum file size in MB", default=100)
        session_id = manager.create_largest(device.id, threshold_mb * 1024**2, 200)
    elif choice == "2":
        session_id = manager.create_duplicates(device.id, 200)
    else:
        raise ValueError("Invalid cleanup queue.")
    console.print(f"Cleanup session ID: {session_id}")
    run_cleanup_session(catalog, paths, devices, session_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="disk-backup", description=APP_NAME)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show the installed application version and exit",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("devices", help="List disks and connected portable devices")
    index = sub.add_parser("index", help="Update or completely rebuild a disk catalog")
    index.add_argument("--device")
    index.add_argument("--full", action="store_true")
    index.add_argument("--yes", action="store_true")
    backup = sub.add_parser("backup", help="Create a resumable filesystem backup")
    backup.add_argument("--source", action="append")
    backup.add_argument("--source-device")
    backup.add_argument("--mtp-path", action="append")
    backup.add_argument("--target-device")
    backup.add_argument("--target-path")
    backup.add_argument("--yes", action="store_true")
    resume = sub.add_parser("resume", help="Resume a backup by ID")
    resume.add_argument("job_id")
    sub.add_parser("jobs", help="List backup jobs")
    review = sub.add_parser("review", help="Review copied, skipped, and cleanup-eligible items")
    review.add_argument("job_id")
    review.add_argument("--details", action="store_true")
    duplicates = sub.add_parser("duplicates", help="Report exact duplicate groups from an indexed disk")
    duplicates.add_argument("--device")
    cleanup = sub.add_parser("cleanup", help="Delete hash-verified source files after backup")
    cleanup.add_argument("job_id")
    cleanup.add_argument("--include-duplicates", action="store_true")
    cleanup.add_argument("--yes", action="store_true")
    return parser


def resumable_backup_jobs(catalog: Catalog) -> list[object]:
    return catalog.connection.execute(
        """SELECT j.*,SUM(i.status IN ('pending','failed')) remaining
        FROM backup_jobs j JOIN backup_items i ON i.job_id=j.id
        WHERE j.status IN ('planned','running','paused','completed_with_errors')
        GROUP BY j.id HAVING remaining>0 ORDER BY j.updated_at DESC"""
    ).fetchall()


def show_resumable_backup_jobs(jobs: list[object]) -> None:
    table = Table(title="Paused or incomplete BACKUP jobs")
    for heading in ("#", "Backup ID", "Source", "Status", "Progress", "Remaining"):
        table.add_column(heading)
    for number, job in enumerate(jobs, 1):
        table.add_row(
            str(number), job["id"], job["source_kind"], job["status"],
            f"{job['completed_files']}/{job['total_files']}", str(job["remaining"]),
        )
    console.print(table)


def choose_backup_to_resume(jobs: list[object], *, startup: bool = False) -> object | None:
    show_resumable_backup_jobs(jobs)
    if startup:
        console.print("To resume backup #1, enter [bold]1[/bold] or [bold]R1[/bold]. "
                      "Pressing Enter accepts S and skips resume.")
    while True:
        answer = Prompt.ask("BACKUP job: number/R<number> resume; S skip/back", default="S")
        try:
            selected = parse_resume_selection(answer, len(jobs))
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            continue
        return None if selected is None else jobs[selected]


def offer_startup_resume(catalog: Catalog, paths: AppPaths) -> None:
    jobs = resumable_backup_jobs(catalog)
    if jobs:
        selected_job = choose_backup_to_resume(jobs, startup=True)
        if selected_job is not None:
            run_backup_job(BackupManager(catalog, paths.staging), catalog, selected_job["id"])

    manage_cleanup_sessions(catalog, paths, startup=True)
    manage_restore_jobs(catalog, paths, startup=True)


def manage_cleanup_sessions(
    catalog: Catalog,
    paths: AppPaths,
    devices: list[Device] | None = None,
    *,
    startup: bool = False,
) -> None:
    manager = CleanupManager(catalog)
    while True:
        sessions = manager.resumable_sessions()
        if not sessions:
            if not startup:
                console.print("No resumable cleanup sessions were found.")
            return
        table = Table(title="Resumable cleanup sessions found")
        for heading in ("#", "Session ID", "Queue", "Pending", "Reclaimed"):
            table.add_column(heading)
        for number, session in enumerate(sessions, 1):
            table.add_row(
                str(number), session["id"], session["queue_type"],
                str(session["pending_count"] or 0), human_bytes(session["freed_bytes"] or 0),
            )
        console.print(table)
        console.print(
            "Enter [bold]R1[/bold] (or 1) to resume, [bold]C1[/bold] to dismiss one, "
            "[bold]CA[/bold] to dismiss all, or [bold]S[/bold] to skip/back."
        )
        answer = Prompt.ask("Cleanup session action", default="S").strip().upper().replace(" ", "")
        if answer in {"S", "X", ""}:
            return
        if answer == "CA":
            if Confirm.ask(
                f"Dismiss all {len(sessions)} cleanup review sessions? No files will be deleted.",
                default=False,
            ):
                for session in sessions:
                    manager.cancel_session(str(session["id"]))
                console.print("All old cleanup review sessions were dismissed. No files were changed.")
                return
            continue
        mode = "R"
        number_text = answer
        if answer[:1] in {"R", "C"}:
            mode, number_text = answer[0], answer[1:]
        if not number_text.isdigit() or not 1 <= int(number_text) <= len(sessions):
            console.print("[yellow]Use R<number>, C<number>, CA, or S.[/yellow]")
            continue
        session = sessions[int(number_text) - 1]
        if mode == "C":
            if Confirm.ask(
                f"Dismiss cleanup session {session['id']}? No files will be deleted.",
                default=False,
            ):
                manager.cancel_session(str(session["id"]))
                console.print("Cleanup review dismissed. No files were changed.")
            continue
        run_cleanup_session(catalog, paths, devices or connected_devices(), str(session["id"]))
        return


def initialize_app_paths(app_root: Path, *, interactive_mode: bool) -> AppPaths | None:
    configuration = activate_configured_state(app_root)
    if configuration is not None:
        return AppPaths.from_state_root(app_root, configuration.state_root)

    default = default_state_root(app_root)
    # Existing installations adopt their historical location without interrupting the user.
    if (default / "catalog.sqlite3").is_file() or not interactive_mode:
        save_state_configuration(app_root, default)
        return AppPaths.from_state_root(app_root, default)

    console.print(Panel(
        f"Disk Backup Manager keeps its index, backup history, resumable jobs, logs and staging data "
        f"in one state folder.\n\nDefault beside the application:\n{default}\n\n"
        f"The selected location is saved in:\n{settings_path(app_root)}",
        title="First-time state location",
    ))
    while True:
        answer = Prompt.ask("State folder; press Enter for default, or X to exit", default=str(default)).strip()
        if answer.upper() == "X":
            return None
        target = resolve_state_path(app_root, answer or str(default))
        try:
            validate_state_destination(default, target)
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            continue
        if not state_directory_is_empty(target) and not (target / "catalog.sqlite3").is_file():
            console.print(
                "[yellow]Choose an empty folder or an existing Disk Backup Manager state folder.[/yellow]"
            )
            continue
        save_state_configuration(app_root, target)
        console.print(f"State location saved: {target}")
        return AppPaths.from_state_root(app_root, target)


def configure_state_location(paths: AppPaths) -> bool:
    """Save a state-location change; return True when the app should exit for restart."""
    default = default_state_root(paths.root)
    console.print(Panel(
        f"Current state folder:\n{paths.state}\n\nSettings file beside the application:\n"
        f"{settings_path(paths.root)}\n\nDefault state folder:\n{default}\n\n"
        "Changing location never deletes the old state folder. A copy-and-switch is performed "
        "on the next start, after the active catalog has closed safely.",
        title="Application state location",
    ))
    action = Prompt.ask("C choose another folder; D use default; S return", choices=["C", "D", "S"], default="S").upper()
    if action == "S":
        return False
    raw_target = str(default) if action == "D" else Prompt.ask("New dedicated state folder").strip()
    target = resolve_state_path(paths.root, raw_target)
    try:
        target = validate_state_destination(paths.state, target)
    except ValueError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        return False
    if target == paths.state:
        console.print("That is already the active state folder.")
        return False

    existing_catalog = target / "catalog.sqlite3"
    if existing_catalog.is_file():
        if not Confirm.ask(
            f"Switch to the existing state catalog at {target} after restart?", default=False,
        ):
            return False
        save_state_configuration(paths.root, target)
    else:
        if not state_directory_is_empty(target):
            console.print("[yellow]The selected folder is not empty. Choose an empty folder.[/yellow]")
            return False
        if not Confirm.ask(
            f"Copy all current state data to {target} and switch on the next start?", default=True,
        ):
            return False
        save_state_configuration(paths.root, target, pending_copy_from=paths.state)
    console.print(
        "State-location change saved. The application will close now. Start it again to complete "
        "the safe switch. The old state folder will be retained as a recovery copy."
    )
    return True


def main_menu_panel() -> Panel:
    menu = Table.grid(padding=(0, 3))
    menu.add_column("Option", justify="right", style="bold cyan", width=7, no_wrap=True)
    menu.add_column("Action", style="white")

    groups = (
        (
            ("1", "Connected devices and storage"),
            ("2", "Update existing device index"),
            ("3", "Complete device rescan"),
        ),
        (
            ("4", "Guided backup to Media vault"),
            ("5", "Resume paused or incomplete backup job"),
        ),
        (
            ("6", "Cleanup and reclaim space"),
            ("7", "Backup history and detailed reports"),
            ("8", "Resume cleanup review session"),
        ),
        (
            ("9", "Restore files from a completed backup"),
            ("10", "Resume paused or incomplete restore job"),
        ),
        (
            ("11", "Search indexed and backed-up files"),
            ("12", "Catalog safety, integrity, and recovery copies"),
            ("13", "Help and safety rules"),
            ("14", "Application state location and settings"),
        ),
    )
    for group_number, group in enumerate(groups):
        if group_number:
            menu.add_row("", "")
        for option, description in group:
            menu.add_row(f"{option}.", description)
    menu.add_row("", "")
    menu.add_row("X.", "Exit")
    return Panel(menu, title=f"[bold]{APP_NAME}[/bold]", padding=(1, 2), expand=True)


def interactive(catalog: Catalog, paths: AppPaths) -> None:
    offer_startup_resume(catalog, paths)
    while True:
        console.print(main_menu_panel())
        action = Prompt.ask("Choose").strip().upper()
        if action == "X":
            console.print("Goodbye. Your catalog and resumable sessions are saved.")
            return
        try:
            if action == "14":
                if configure_state_location(paths):
                    return
                pause_before_menu()
                continue
            devices = connected_devices()
            if action == "1":
                show_devices(devices)
            elif action in {"2", "3"}:
                do_index(catalog, paths, choose_device(devices, "Device number", filesystem_only=True),
                         action == "3", False)
            elif action == "4":
                guided_backup(catalog, paths, devices)
            elif action == "5":
                jobs = resumable_backup_jobs(catalog)
                if not jobs:
                    console.print("No resumable backup jobs were found.")
                else:
                    selected_job = choose_backup_to_resume(jobs)
                    if selected_job is not None:
                        run_backup_job(BackupManager(catalog, paths.staging), catalog, selected_job["id"])
            elif action == "6":
                start_cleanup(catalog, paths, devices)
            elif action == "7":
                show_backup_history(catalog)
            elif action == "8":
                manage_cleanup_sessions(catalog, paths, devices)
            elif action == "9":
                start_restore(catalog, paths, devices)
            elif action == "10":
                manage_restore_jobs(catalog, paths)
            elif action == "11":
                search_files(catalog)
            elif action == "12":
                show_catalog_safety(catalog)
            elif action == "13":
                console.print(Panel(
                    "Backup first, verify SHA-256 second, delete third.\n"
                    "No filename-only duplicate is deletable. Phone/tablet deletion requires a verified backup.\n"
                    "Restore verifies backup proof before copying and never overwrites a different existing file.\n"
                    f"Default vault: {default_vault_root(paths.root)}; files are placed directly in category folders.",
                    title="Safety rules",
                ))
            else:
                console.print("Choose 1-14 or X.")
        except (ValueError, FileNotFoundError, KeyError, RuntimeError, OSError) as exc:
            console.print(f"[red]Operation stopped safely: {exc}[/red]")
        pause_before_menu()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog: Catalog | None = None
    try:
        app_root = application_root().resolve()
        paths = initialize_app_paths(app_root, interactive_mode=not bool(args.command))
        if paths is None:
            return 0
        paths.ensure()
        catalog = Catalog(paths.database)
        devices = connected_devices()
        if not args.command:
            interactive(catalog, paths)
        elif args.command == "devices":
            show_devices(devices)
        elif args.command == "index":
            device = next((item for item in devices if item.id == args.device), None) if args.device else choose_device(devices, "Disk number", filesystem_only=True)
            if not device:
                raise ValueError("Requested device is not connected.")
            do_index(catalog, paths, device, args.full, args.yes)
        elif args.command == "backup":
            source_device = next((item for item in devices if item.id == args.source_device), None) if args.source_device else None
            target = next((item for item in devices if item.id == args.target_device), None) if args.target_device else choose_device(devices, "Target disk number", filesystem_only=True)
            if not target:
                raise ValueError("Requested target device is not connected.")
            target_path = Path(args.target_path) if args.target_path else Path(target.root) / "Media"
            if source_device and source_device.kind == "mtp":
                selected = args.mtp_path or [name for name, is_folder, _ in list_children(source_device.id) if is_folder]
                do_mtp_backup(catalog, paths, source_device, selected, target, target_path, args.yes)
            else:
                sources = [Path(value) for value in args.source] if args.source else ([Path(source_device.root)] if source_device else [Path(Prompt.ask("Source path"))])
                do_backup(catalog, paths, sources, target, target_path, args.yes)
        elif args.command == "resume":
            run_backup_job(BackupManager(catalog, paths.staging), catalog, args.job_id)
        elif args.command == "jobs":
            show_jobs(catalog)
        elif args.command == "review":
            review_job(catalog, args.job_id, args.details)
        elif args.command == "duplicates":
            device = next((item for item in devices if item.id == args.device), None) if args.device else choose_device(devices, "Indexed disk number", filesystem_only=True)
            if not device:
                raise ValueError("Requested device is not connected.")
            show_duplicates(catalog, device.id)
        elif args.command == "cleanup":
            review_job(catalog, args.job_id)
            console.print("[bold red]Cleanup permanently deletes source files only after rechecking source and target SHA-256 hashes.[/bold red]")
            if args.yes or Confirm.ask("Proceed with verified source cleanup?", default=False):
                deleted, errors = BackupManager(catalog, paths.staging).cleanup_sources(args.job_id, include_duplicates=args.include_duplicates)
                console.print(f"Deleted {deleted} verified source files; {len(errors)} errors.")
                for error in errors[:20]:
                    console.print(f"[yellow]{error}[/yellow]")
        return 0
    except (ValueError, FileNotFoundError, KeyError, RuntimeError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        return 2
    finally:
        if catalog is not None:
            catalog.close()


if __name__ == "__main__":
    sys.exit(main())
