from __future__ import annotations

import tempfile
import unittest
import sqlite3
import os
import sys
from datetime import date, datetime
from unittest import mock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))

from disk_backup.backup import BackupManager, estimate_selected, validate_target_location
from disk_backup.cleanup import CleanupManager
from disk_backup.config import AppPaths, application_root, default_vault_root
from disk_backup.database import Catalog, SCHEMA
from disk_backup.devices import Device
from disk_backup.indexer import index_volume
from disk_backup.hashing import sha256_file
from disk_backup.mtp import (
    MtpEntry, _delete_shell_item_silently, _delete_shell_items_silently,
    _item_size, _parse_display_size,
    collect_entry_groups, open_entry,
)
from disk_backup.restore import RestoreManager
from disk_backup.search import index_health, open_stored_file, search_catalog
from disk_backup.selection import (
    SourceFile, apply_category_selection, apply_numbered_selection,
    categorize_destinations, collect_filesystem_sources, parse_resume_selection,
)
from disk_backup.state_config import (
    activate_configured_state,
    configured_state,
    default_state_root,
    save_state_configuration,
)


def make_device(path: Path, identifier: str) -> Device:
    return Device(identifier, path.name, "fixed", str(path), 10**9, 5 * 10**8)


class CoreTests(unittest.TestCase):
    def temporary_directory(self) -> tempfile.TemporaryDirectory[str]:
        test_root = Path.cwd() / "work" / "test-temp"
        test_root.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=test_root)

    def test_default_vault_uses_application_volume(self) -> None:
        with self.temporary_directory() as directory:
            application = Path(directory) / "portable-app"
            expected = Path(application.resolve().anchor) / "Media"
            self.assertEqual(default_vault_root(application), expected)

    def test_default_state_configuration_is_portable_beside_application(self) -> None:
        with self.temporary_directory() as directory:
            application = Path(directory) / "portable-app"
            application.mkdir()
            state = default_state_root(application)

            setting = save_state_configuration(application, state)
            configuration = configured_state(application)
            paths = AppPaths.from_state_root(application, state)

            self.assertIn('"state_path": "state"', setting.read_text(encoding="utf-8"))
            self.assertIsNotNone(configuration)
            self.assertEqual(configuration.state_root, state.resolve())
            self.assertEqual(paths.state, state.resolve())
            self.assertEqual(paths.database, state.resolve() / "catalog.sqlite3")

    def test_pending_state_copy_activates_and_retains_old_state(self) -> None:
        with self.temporary_directory() as directory:
            application = Path(directory) / "portable-app"
            current = application / "state"
            destination = application / "central-state"
            current.mkdir(parents=True)
            catalog = Catalog(current / "catalog.sqlite3")
            catalog.close()
            (current / "logs").mkdir()
            (current / "logs" / "application.log").write_text("retained", encoding="utf-8")
            save_state_configuration(
                application,
                destination,
                pending_copy_from=current,
            )

            configuration = activate_configured_state(application)

            self.assertIsNotNone(configuration)
            self.assertEqual(configuration.state_root, destination.resolve())
            self.assertIsNone(configuration.pending_copy_from)
            self.assertTrue((destination / "catalog.sqlite3").is_file())
            self.assertEqual(
                (destination / "logs" / "application.log").read_text(encoding="utf-8"),
                "retained",
            )
            self.assertTrue((current / "catalog.sqlite3").is_file())
            self.assertIsNone(configured_state(application).pending_copy_from)

    def test_mtp_fast_folder_include_and_skip_syntax(self) -> None:
        children = [("Pictures", True, 0), ("Download", True, 0), ("DCIM", True, 0)]
        self.assertEqual(
            [item[0] for item in apply_numbered_selection(children, "I 1 3") or []],
            ["Pictures", "DCIM"],
        )
        self.assertEqual(
            [item[0] for item in apply_numbered_selection(children, "1 3") or []],
            ["Pictures", "DCIM"],
        )
        self.assertEqual(
            [item[0] for item in apply_numbered_selection(children, "S 2") or []],
            ["Pictures", "DCIM"],
        )
        self.assertEqual(apply_numbered_selection(children, "X"), None)

    def test_category_shortcuts_select_photos_videos_or_both(self) -> None:
        available = ["videos", "images", "audio", "documents", "archives_installers", "other"]
        self.assertEqual(apply_category_selection(available, "P"), {"images"})
        self.assertEqual(apply_category_selection(available, "V"), {"videos"})
        self.assertEqual(apply_category_selection(available, "PV"), {"images", "videos"})
        self.assertEqual(apply_category_selection(available, "AU"), {"audio"})
        self.assertEqual(apply_category_selection(available, "D"), {"documents"})
        self.assertEqual(apply_category_selection(available, "V AU D"), {"videos", "audio", "documents"})
        self.assertEqual(apply_category_selection(available, "1 4"), {"videos", "documents"})
        self.assertIsNone(apply_category_selection(available, "X"))

    def test_first_mtp_estimate_accounts_for_realistic_phone_throughput(self) -> None:
        with self.temporary_directory() as directory:
            catalog = Catalog(Path(directory) / "catalog.sqlite3")
            entries = [
                SourceFile(
                    f"DCIM/{number}.heic", f"Photos/{number}.heic", f"{number}.heic",
                    14 * 1024**3 // 650, 1, ".heic", "images", "Camera / DCIM", "DCIM",
                )
                for number in range(650)
            ]
            estimate = estimate_selected(catalog, entries, "mtp")
            self.assertGreater(estimate.seconds, 30 * 60)
            self.assertLess(estimate.seconds, 60 * 60)
            catalog.close()

    def test_resume_selection_accepts_number_or_explicit_r_prefix(self) -> None:
        self.assertEqual(parse_resume_selection("1", 3), 0)
        self.assertEqual(parse_resume_selection("R 2", 3), 1)
        self.assertIsNone(parse_resume_selection("S", 3))

    def test_incremental_index_reuses_unchanged_hash(self) -> None:
        with self.temporary_directory() as directory:
            tmp_path = Path(directory)
            root = tmp_path / "disk"
            root.mkdir()
            (root / "a.txt").write_text("alpha", encoding="utf-8")
            catalog = Catalog(tmp_path / "catalog.sqlite3")
            device = make_device(root, "test:index")
            first = index_volume(catalog, device)
            second = index_volume(catalog, device)
            self.assertEqual(first.hashed_files, 1)
            self.assertEqual(second.hashed_files, 0)
            (root / "a.txt").write_text("changed", encoding="utf-8")
            third = index_volume(catalog, device)
            self.assertEqual(third.hashed_files, 1)
            catalog.close()

    def test_catalog_manual_backup_is_consistent_and_active_file_is_protected(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            database = root / "catalog.sqlite3"
            catalog = Catalog(database)
            backup = catalog.create_backup("test")
            self.assertTrue(backup.is_file())
            check = sqlite3.connect(backup)
            try:
                self.assertEqual(check.execute("PRAGMA quick_check").fetchone()[0], "ok")
            finally:
                check.close()
            if os.name == "nt":
                with self.assertRaises(PermissionError):
                    database.unlink()
            catalog.close()
            self.assertTrue(database.is_file())

    def test_file_search_returns_nearest_name_and_index_freshness(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory) / "disk"
            root.mkdir()
            expected = root / "Vacation_Photo_2026.jpg"
            expected.write_bytes(b"photo")
            catalog = Catalog(Path(directory) / "catalog.sqlite3")
            device = make_device(root, "search:disk")
            index_volume(catalog, device)

            results = search_catalog(catalog, "vacaton foto")

            self.assertTrue(results)
            self.assertEqual(results[0].name, expected.name)
            health = index_health(catalog)
            self.assertEqual(health[0].volume_id, device.id)
            self.assertIn("Recent", health[0].recommendation)
            catalog.close()

    def test_file_search_supports_folder_category_and_modified_date_filters(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory) / "disk"
            trip = root / "Photos" / "Coorg Trip"
            trip.mkdir(parents=True)
            photo = trip / "waterfall.jpg"
            photo.write_bytes(b"photo")
            modified = datetime(2026, 6, 2, 12, 0).timestamp()
            os.utime(photo, (modified, modified))
            catalog = Catalog(Path(directory) / "catalog.sqlite3")
            index_volume(catalog, make_device(root, "search:filters"))

            place_results = search_catalog(catalog, "coorg")
            filtered = search_catalog(
                catalog, categories={"images"}, location="Coorg Trip",
                modified_from=date(2026, 6, 1), modified_to=date(2026, 6, 30),
            )
            excluded = search_catalog(
                catalog, categories={"videos"}, location="Coorg Trip",
            )

            self.assertEqual(place_results[0].name, photo.name)
            self.assertEqual(filtered[0].location, str(photo))
            self.assertTrue(filtered[0].modified_at.startswith("2026-06-02"))
            self.assertEqual(excluded, [])
            catalog.close()

    def test_search_open_blocks_executables_and_opens_safe_files(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            photo = root / "photo.jpg"
            program = root / "program.exe"
            photo.write_bytes(b"photo")
            program.write_bytes(b"not executable")
            if os.name == "nt":
                with mock.patch("disk_backup.search.os.startfile", create=True) as opener:
                    self.assertEqual(open_stored_file(str(photo)), photo)
                    opener.assert_called_once_with(str(photo))
            with self.assertRaises(PermissionError):
                open_stored_file(str(program))

    def test_backup_skips_exact_duplicate_and_can_resume(self) -> None:
        with self.temporary_directory() as directory:
            tmp_path = Path(directory)
            source = tmp_path / "source"
            target = tmp_path / "target"
            source.mkdir()
            target.mkdir()
            (source / "same.bin").write_bytes(b"same content")
            (target / "existing.bin").write_bytes(b"same content")
            catalog = Catalog(tmp_path / "catalog.sqlite3")
            target_device = make_device(target, "test:target")
            index_volume(catalog, target_device)
            manager = BackupManager(catalog, tmp_path / "staging")
            job_id = manager.create_job([source], target_device, target / "Backups")
            result = manager.run_job(job_id)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(result.copied, 0)
            resumed = manager.run_job(job_id)
            self.assertEqual(resumed.skipped, 1)
            catalog.close()

    def test_new_file_is_copied_and_verified_cleanup_is_guarded(self) -> None:
        with self.temporary_directory() as directory:
            tmp_path = Path(directory)
            source = tmp_path / "source"
            target = tmp_path / "target"
            source.mkdir()
            target.mkdir()
            original = source / "new.txt"
            original.write_text("new content", encoding="utf-8")
            catalog = Catalog(tmp_path / "catalog.sqlite3")
            target_device = make_device(target, "test:new-target")
            index_volume(catalog, target_device)
            manager = BackupManager(catalog, tmp_path / "staging")
            job_id = manager.create_job([source], target_device, target / "Backups")
            result = manager.run_job(job_id)
            self.assertEqual(result.copied, 1)
            copied = target / "Backups" / "source" / "new.txt"
            self.assertEqual(copied.read_text(encoding="utf-8"), "new content")
            deleted, errors = manager.cleanup_sources(job_id)
            self.assertEqual((deleted, errors), (1, []))
            self.assertFalse(original.exists())
            catalog.close()

    def test_deleted_files_can_be_restored_to_original_filesystem_paths(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "source" / "report.txt"
            source.parent.mkdir()
            source.write_text("restore me", encoding="utf-8")
            vault = root / "vault"
            catalog = Catalog(root / "catalog.sqlite3")
            device = make_device(root, "restore:disk")
            backup = BackupManager(catalog, root / "staging")
            job_id = backup.create_job(
                [source], device, vault,
                included_entries=categorize_destinations(collect_filesystem_sources([source])),
            )
            backup.run_job(job_id)
            deleted, errors = backup.cleanup_sources(job_id)
            self.assertEqual((deleted, errors), (1, []))

            restore = RestoreManager(catalog, root / "staging")
            restore_id = restore.create_job(job_id, device, {"documents"}, deleted_only=True)
            result = restore.run_job(restore_id)

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.restored, 1)
            self.assertEqual(source.read_text(encoding="utf-8"), "restore me")
            cleanup = catalog.connection.execute(
                "SELECT cleanup_status FROM backup_items WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            self.assertEqual(cleanup, "not_requested")
            catalog.close()

    def test_restore_never_overwrites_a_different_existing_file(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "source" / "report.txt"
            source.parent.mkdir()
            source.write_text("backed up", encoding="utf-8")
            catalog = Catalog(root / "catalog.sqlite3")
            device = make_device(root, "restore:conflict")
            backup = BackupManager(catalog, root / "staging")
            job_id = backup.create_job(
                [source], device, root / "vault",
                included_entries=categorize_destinations(collect_filesystem_sources([source])),
            )
            backup.run_job(job_id)
            backup.cleanup_sources(job_id)
            source.write_text("new phone content", encoding="utf-8")

            restore = RestoreManager(catalog, root / "staging")
            restore_id = restore.create_job(job_id, device, {"documents"}, deleted_only=True)
            result = restore.run_job(restore_id)

            self.assertEqual(result.conflicts, 1)
            self.assertEqual(source.read_text(encoding="utf-8"), "new phone content")
            catalog.close()

    def test_mtp_restore_preserves_original_logical_path_and_verifies_readback(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            proof = root / "photo.jpg"
            proof.write_bytes(b"verified photo")
            catalog = Catalog(root / "catalog.sqlite3")
            disk = make_device(root, "restore:proof-disk")
            backup = BackupManager(catalog, root / "staging")
            source_entry = MtpEntry(
                "DCIM/Camera/photo.jpg", "photo.jpg", proof.stat().st_size, 1,
                ".jpg", "images", "JPEG", object(),
            )
            job_id = backup.create_mtp_job(
                "mtp:original|Internal storage", ["DCIM"], [source_entry],
                disk, root / "vault",
            )
            with catalog.transaction() as connection:
                connection.execute("UPDATE backup_jobs SET status='completed' WHERE id=?", (job_id,))
                connection.execute(
                    """UPDATE backup_items SET status='copied',sha256=?,target_path=?,
                    cleanup_status='deleted' WHERE job_id=?""",
                    (sha256_file(proof), str(proof), job_id),
                )
            phone = Device(
                "mtp:replacement|Internal storage", "Replacement phone", "mtp", "",
                10**9, 5 * 10**8,
            )
            restore = RestoreManager(catalog, root / "staging")
            restore_id = restore.create_job(job_id, phone, {"images"}, deleted_only=True)

            def fake_readback(_entry, directory, timeout_seconds=None):
                directory.mkdir(parents=True, exist_ok=True)
                output = directory / "photo.jpg"
                output.write_bytes(proof.read_bytes())
                return output

            restored_entry = MtpEntry(
                "DCIM/Camera/photo.jpg", "photo.jpg", proof.stat().st_size, 1,
                ".jpg", "images", "JPEG", object(),
            )
            with mock.patch("disk_backup.restore.collect_entries", side_effect=FileNotFoundError), \
                 mock.patch("disk_backup.restore.copy_file_to_device", return_value=restored_entry) as copy_to, \
                 mock.patch("disk_backup.restore.copy_entry_to", side_effect=fake_readback):
                result = restore.run_job(restore_id)

            self.assertEqual(result.restored, 1)
            copy_to.assert_called_once()
            self.assertEqual(copy_to.call_args.args[2], "DCIM/Camera/photo.jpg")
            cleanup = catalog.connection.execute(
                "SELECT cleanup_status FROM backup_items WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            self.assertEqual(cleanup, "deleted")
            catalog.close()

    def test_target_inside_source_is_rejected(self) -> None:
        with self.temporary_directory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            with self.assertRaises(ValueError):
                validate_target_location([source], source / "Backups")

    def test_mtp_display_sizes_are_parsed_for_estimates(self) -> None:
        self.assertEqual(_parse_display_size("29.7 KB"), int(29.7 * 1024))
        self.assertEqual(_parse_display_size("1.25 GB"), int(1.25 * 1024**3))

    def test_mtp_size_prefers_exact_system_property(self) -> None:
        item = mock.Mock()
        item.ExtendedProperty.return_value = 12_345
        item.Size = 0
        namespace = mock.Mock()
        namespace.GetDetailsOf.return_value = "12.1 KB"
        self.assertEqual(_item_size(item, namespace), 12_345)

    def test_mtp_inventory_preserves_top_level_groups(self) -> None:
        folder = mock.Mock(IsFolder=True)
        location = mock.Mock(storage_item=object())
        first = MtpEntry("DCIM/a.jpg", "a.jpg", 10, 0, ".jpg", "images", "", object())
        second = MtpEntry("DCIM/b.jpg", "b.jpg", 20, 0, ".jpg", "images", "", object())
        progress: list[tuple[str, int, int]] = []

        def fake_walk(_item, _prefix, output, on_entry) -> None:
            for entry in (first, second):
                output.append(entry)
                on_entry(entry)

        with mock.patch("disk_backup.mtp.open_location", return_value=location), \
             mock.patch("disk_backup.mtp.resolve_item", return_value=folder), \
             mock.patch("disk_backup.mtp._walk", side_effect=fake_walk):
            groups = collect_entry_groups(
                "mtp:test|storage", ["DCIM"],
                lambda path, count, size: progress.append((path, count, size)),
            )

        self.assertEqual(groups, [("DCIM", [first, second])])
        self.assertEqual(progress[-1], ("DCIM", 2, 30))

    def test_mtp_open_invokes_default_windows_verb(self) -> None:
        location = mock.Mock(storage_item=object())
        item = mock.Mock()
        with mock.patch("disk_backup.mtp.open_location", return_value=location), \
             mock.patch("disk_backup.mtp.resolve_item", return_value=item):
            open_entry("mtp:phone|storage", "DCIM/video.mp4")
            item.InvokeVerb.assert_called_once_with()

    def test_mtp_silent_delete_uses_ifileoperation_without_confirmation(self) -> None:
        from win32com.shell import shell, shellcon

        item = object()
        operation = mock.Mock()
        operation.GetAnyOperationsAborted.return_value = False
        with mock.patch.object(shell, "SHGetIDListFromObject", return_value="pidl"), \
             mock.patch.object(shell, "SHCreateItemFromIDList", return_value="shell-item"), \
             mock.patch("pythoncom.CoCreateInstance", return_value=operation):
            _delete_shell_item_silently(item)

        operation.DeleteItem.assert_called_once_with("shell-item", None)
        operation.PerformOperations.assert_called_once_with()
        flags = operation.SetOperationFlags.call_args.args[0]
        self.assertTrue(flags & shellcon.FOF_NOCONFIRMATION)
        self.assertTrue(flags & shellcon.FOF_SILENT)
        self.assertTrue(flags & shellcon.FOF_NOERRORUI)

    def test_mtp_bulk_delete_queues_one_shell_operation(self) -> None:
        from win32com.shell import shell

        operation = mock.Mock()
        operation.GetAnyOperationsAborted.return_value = False
        with mock.patch.object(
            shell, "SHGetIDListFromObject", side_effect=["pidl-1", "pidl-2"],
        ), mock.patch.object(
            shell, "SHCreateItemFromIDList", side_effect=["shell-1", "shell-2"],
        ), mock.patch("pythoncom.CoCreateInstance", return_value=operation):
            _delete_shell_items_silently(["item-1", "item-2"])

        self.assertEqual(
            operation.DeleteItem.call_args_list,
            [mock.call("shell-1", None), mock.call("shell-2", None)],
        )
        operation.PerformOperations.assert_called_once_with()

    def test_mtp_cleanup_batch_records_only_confirmed_deletions(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            proof = root / "proof.mp4"
            proof.write_bytes(b"verified-video")
            digest = sha256_file(proof)
            catalog = Catalog(root / "catalog.sqlite3")
            session_id = "cleanup-batch"
            with catalog.transaction() as connection:
                connection.execute(
                    """INSERT INTO cleanup_sessions(
                    id,source_device_id,source_kind,source_ref,queue_type,status,
                    threshold_bytes,limit_count,created_at,updated_at)
                    VALUES(?,?,?,?,?,'running',0,1,'now','now')""",
                    (session_id, "mtp:phone|storage", "mtp", "backup_job:test", "backed_up"),
                )
                connection.execute(
                    """INSERT INTO cleanup_items(
                    session_id,source_path,source_name,size,mtime_ns,extension,category,
                    sha256,proof_path,rank) VALUES(?,?,?,?,?,?,?,?,?,1)""",
                    (session_id, "DCIM/video.mp4", "video.mp4", 14, 123, ".mp4",
                     "videos", digest, str(proof)),
                )
            live = MtpEntry(
                "DCIM/video.mp4", "video.mp4", 14, 123, ".mp4", "videos", "", object(),
            )
            manager = CleanupManager(catalog)
            with mock.patch(
                "disk_backup.cleanup.resolve_delete_entries", return_value=([live], {}),
            ), mock.patch(
                "disk_backup.cleanup.delete_resolved_entries",
                return_value=({"DCIM/video.mp4"}, set()),
            ):
                deleted, errors, reclaimed = manager.delete_proven_mtp_batch(
                    session_id, ["DCIM/video.mp4"], {},
                )

            self.assertEqual(deleted, ["DCIM/video.mp4"])
            self.assertEqual(errors, {})
            self.assertEqual(reclaimed, 14)
            row = catalog.connection.execute(
                "SELECT status,action,freed_bytes FROM cleanup_items WHERE session_id=?",
                (session_id,),
            ).fetchone()
            self.assertEqual((row["status"], row["action"], row["freed_bytes"]),
                             ("deleted", "bulk_delete", 14))
            catalog.close()

    def test_legacy_catalog_migrates_and_classifies_mp3(self) -> None:
        with self.temporary_directory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO volumes VALUES(?,?,?,?,?)",
                ("legacy", "Legacy", "X:/", "fixed", "now"),
            )
            scan_id = connection.execute(
                "INSERT INTO scans(volume_id,mode,started_at,status) VALUES('legacy','incremental','now','completed')"
            ).lastrowid
            connection.execute(
                """INSERT INTO files(volume_id,relative_path,name,size,mtime_ns,sha256,last_seen_scan)
                VALUES('legacy','Music/song.MP3','song.MP3',4,1,'hash',?)""", (scan_id,),
            )
            connection.commit()
            connection.close()
            catalog = Catalog(database)
            row = catalog.connection.execute(
                "SELECT extension,category FROM files WHERE volume_id='legacy'"
            ).fetchone()
            self.assertEqual((row["extension"], row["category"]), (".mp3", "audio"))
            self.assertEqual(catalog.connection.execute("PRAGMA user_version").fetchone()[0], 4)
            catalog.close()

    def test_category_destinations_use_media_layout(self) -> None:
        with self.temporary_directory() as directory:
            source = Path(directory) / "song.mp3"
            source.write_bytes(b"audio")
            entries = collect_filesystem_sources([source])
            planned = categorize_destinations(entries, "My Tablet")
            self.assertEqual(planned[0].relative_path, "Recordings/song.mp3")

    def test_selected_backup_writes_into_category_device_path(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "source" / "song.mp3"
            source.parent.mkdir()
            source.write_bytes(b"music")
            vault = root / "Media"
            target_device = make_device(root, "media:vault")
            catalog = Catalog(root / "catalog.sqlite3")
            catalog.upsert_volume(target_device.id, target_device.label, target_device.root, target_device.kind)
            planned = categorize_destinations(collect_filesystem_sources([source]), "My Phone")
            manager = BackupManager(catalog, root / "staging")
            job_id = manager.create_job([source], target_device, vault, included_entries=planned)
            result = manager.run_job(job_id)
            self.assertEqual(result.copied, 1)
            self.assertEqual((vault / "Recordings" / "song.mp3").read_bytes(), b"music")
            catalog.close()

    def test_flat_category_backup_numbers_conflicting_filenames(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            first = root / "first" / "photo.jpg"
            second = root / "second" / "photo.jpg"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"first image")
            second.write_bytes(b"different image")
            vault = root / "Media"
            catalog = Catalog(root / "catalog.sqlite3")
            target_device = make_device(root, "flat-collision-target")
            catalog.upsert_volume(target_device.id, target_device.label, target_device.root, target_device.kind)
            planned = categorize_destinations(collect_filesystem_sources([first, second]))
            manager = BackupManager(catalog, root / "staging")
            job_id = manager.create_job([first, second], target_device, vault, included_entries=planned)
            result = manager.run_job(job_id)
            self.assertEqual(result.copied, 2)
            self.assertEqual((vault / "Photos" / "photo.jpg").read_bytes(), b"first image")
            self.assertEqual(
                (vault / "Photos" / "photo (backup 1).jpg").read_bytes(),
                b"different image",
            )
            catalog.close()

    def test_source_category_route_can_override_default_target_folder(self) -> None:
        entry = SourceFile(
            "Recordings/Call/call.mp3", "Recordings/Call/call.mp3", "call.mp3",
            100, 1, ".mp3", "audio", "Recordings", "Recordings/Call",
        )
        planned = categorize_destinations(
            [entry], destination_folders={("Recordings/Call", "audio"): "Call Recordings"},
        )
        self.assertEqual(planned[0].relative_path, "Call Recordings/call.mp3")

    def test_new_mtp_job_reuses_live_inventory(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            catalog = Catalog(root / "catalog.sqlite3")
            target = root / "vault"
            target.mkdir()
            target_device = make_device(root, "mtp-cache-target")
            entry = MtpEntry(
                "DCIM/a.jpg", "a.jpg", 10, 1, ".jpg", "images", "JPEG", object(),
            )
            manager = BackupManager(catalog, root / "staging")
            job_id = manager.create_mtp_job(
                "mtp:phone|storage", ["DCIM"], [entry], target_device, target,
            )
            self.assertIs(manager._live_mtp_entries[job_id]["DCIM/a.jpg"], entry)
            catalog.close()

    def test_cleanup_created_from_job_is_linked_to_backup_id(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "source" / "file.txt"
            source.parent.mkdir()
            source.write_text("verified", encoding="utf-8")
            vault = root / "vault"
            catalog = Catalog(root / "catalog.sqlite3")
            target_device = make_device(root, "cleanup-job-target")
            manager = BackupManager(catalog, root / "staging")
            job_id = manager.create_job(
                [source], target_device, vault,
                included_entries=categorize_destinations(collect_filesystem_sources([source])),
            )
            manager.run_job(job_id)
            session_id = CleanupManager(catalog).create_from_job(job_id)
            session = CleanupManager(catalog).session(session_id)
            self.assertEqual(session["source_ref"], f"backup_job:{job_id}")
            self.assertEqual(session["queue_type"], "backed_up")
            catalog.close()

    def test_cancel_cleanup_session_preserves_pending_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first.bin").write_bytes(b"duplicate")
            (root / "second.bin").write_bytes(b"duplicate")
            catalog = Catalog(root / "catalog.sqlite3")
            device = make_device(root, "cleanup:cancel")
            index_volume(catalog, device)
            manager = CleanupManager(catalog)
            session_id = manager.create_duplicates(device.id)
            pending_before = len(manager.pending_items(session_id))

            manager.cancel_session(session_id)

            self.assertEqual(manager.session(session_id)["status"], "cancelled")
            self.assertEqual(len(manager.pending_items(session_id)), pending_before)
            self.assertFalse(manager.resumable_sessions())
            self.assertTrue((root / "first.bin").is_file())
            self.assertTrue((root / "second.bin").is_file())
            catalog.close()

    def test_duplicate_cleanup_queue_deletes_only_with_hash_proof(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory) / "disk"
            root.mkdir()
            (root / "first.bin").write_bytes(b"duplicate")
            (root / "second.bin").write_bytes(b"duplicate")
            catalog = Catalog(Path(directory) / "catalog.sqlite3")
            device = make_device(root, "cleanup:disk")
            index_volume(catalog, device)
            manager = CleanupManager(catalog)
            session_id = manager.create_duplicates(device.id)
            pending = manager.pending_items(session_id)
            self.assertEqual(len(pending), 1)
            freed = manager.delete_proven_item(session_id, pending[0]["source_path"])
            self.assertEqual(freed, len(b"duplicate"))
            self.assertEqual(len(list(root.glob("*.bin"))), 1)
            catalog.close()

    def test_mtp_largest_cleanup_queue_starts_without_delete_proof(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            catalog = Catalog(root / "catalog.sqlite3")
            entries = [
                SourceFile(
                    "DCIM/large.mp4", "DCIM/large.mp4", "large.mp4", 200 * 1024**2,
                    1, ".mp4", "videos", "Camera / DCIM", "DCIM",
                ),
                SourceFile(
                    "DCIM/small.jpg", "DCIM/small.jpg", "small.jpg", 1 * 1024**2,
                    1, ".jpg", "images", "Camera / DCIM", "DCIM",
                ),
            ]
            manager = CleanupManager(catalog)
            session_id = manager.create_largest_mtp(
                "mtp:phone|storage", entries, 100 * 1024**2,
            )
            session = manager.session(session_id)
            pending = manager.pending_items(session_id)
            self.assertEqual(session["source_kind"], "mtp")
            self.assertEqual(len(pending), 1)
            self.assertIsNone(pending[0]["sha256"])
            self.assertIsNone(pending[0]["proof_path"])
            catalog.close()

    def test_frozen_executable_in_dist_uses_project_catalog(self) -> None:
        with self.temporary_directory() as directory:
            project = Path(directory) / "application"
            executable = project / "dist" / "DiskBackupManager.exe"
            executable.parent.mkdir(parents=True)
            (project / "state").mkdir()
            (project / "state" / "catalog.sqlite3").touch()
            environment = dict(os.environ)
            environment.pop("DISK_BACKUP_HOME", None)
            with mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(executable)):
                self.assertEqual(application_root(), project)

    def test_frozen_executable_setting_wins_over_retained_legacy_state(self) -> None:
        with self.temporary_directory() as directory:
            project = Path(directory) / "application"
            executable_root = project / "dist"
            executable = executable_root / "DiskBackupManager.exe"
            executable_root.mkdir(parents=True)
            (project / "state").mkdir()
            (project / "state" / "catalog.sqlite3").touch()
            (executable_root / "disk-backup-settings.json").write_text(
                '{"schema_version": 1, "state_path": "state"}\n',
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment.pop("DISK_BACKUP_HOME", None)
            with mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(executable)):
                self.assertEqual(application_root(), executable_root)


if __name__ == "__main__":
    unittest.main()
