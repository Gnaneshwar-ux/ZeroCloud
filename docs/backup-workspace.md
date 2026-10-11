# ZeroCloud: Backup & protect

Open **Backup & protect** in the left sidebar. This is a dedicated workspace using ZeroCloud's existing blue, lilac, mint and amber styling. Your media library stays separate and is not replaced by the backup catalog.

## Organized screens

- **Overview:** storage devices, backup totals and three starting actions.
- **New backup:** select sources, review inventory, then choose destinations and preview the plan. Planning does not copy files. Start copying explicitly.
- **Jobs & history:** separate tabs for backups, restores, cleanup reviews and scans. Older history and file details are paginated. Pause an active job or resume a saved job here.
- **Restore:** preview selected categories, optionally only files removed by cleanup, then restore to original paths or an explicitly chosen compatible location. Existing conflicting files are not overwritten.
- **Cleanup:** review backed-up sources, exact duplicates or large files. Keep selected files, ignore a folder, cancel a review, or back up selected candidates first. Deletion is always a separate confirmed action.
- **Search backups:** search the catalog by name/path, including tolerant spelling matches, category, location and modification dates. Open or reveal catalogued files; executable and active-content files cannot be launched through this screen.
- **Settings:** portable backup state, import, recovery copies and incremental/full SHA-256 indexing.

## Sources, selection and destinations

Use folders, disks, removable drives or Windows-visible MTP phones/tablets. Phone discovery requires an unlocked device in file-transfer mode and a working Windows driver. Choose categories, source folder groups, specific folders and individual file exclusions. Use the organized category layout or preserve the original folder tree; optional routing sends categories and source groups to safe relative subfolders.

The inventory review shows counts and bytes without rendering every media file. The plan includes an estimate. Backup uses SHA-256 content matching rather than names alone, recognizes previously verified copies, and verifies newly copied destinations before recording success. Backup copies are intentionally full originals; they are not media thumbnail caches.

## Pause, restart and safety

Only one disk-heavy backup/index/restore/cleanup task runs in this workspace at a time, in a separate local process from media indexing. Pause takes effect at a safe boundary; the current file can finish. Saved jobs survive restart and never start or delete files automatically. Resume backup, restore or scan work explicitly. Cleanup requires a fresh review and confirmation after restart.

Cleanup requires an independent recorded backup and rechecks the actual source and retained proof. A source is never its own backup. Selected deletion paths are shown first, the user must type **DELETE**, and the desktop requests confirmation again. Changed files, missing proofs, restore conflicts and unsafe destinations are kept/refused and reported. MTP cleanup also reads and hashes the live phone source before deletion. A verified backup is still not a substitute for a second independent copy against hardware failure.

## Portable state and importing the existing manager

Backup state stores `catalog.sqlite3`, job history, inventories and recovery data. It is separate from **Library storage** and each media source's optional thumbnail cache.

**Move backup state folder** selects a dedicated empty folder, makes a consistent database copy and leaves the old state folder intact. Do not choose a drive root or nest it inside a source/destination. Temporary MTP staging can use space while a job runs.

**Import Disk Backup Manager state** selects the folder containing its `catalog.sqlite3`. ZeroCloud imports a copy into a new state folder; the original manager and original catalog are not modified. Import does not copy the actual backup vaults, rewrite their absolute paths, or begin any job. The stored disks/files must still be available at their recorded paths to resume or restore. **Create catalog recovery copy** backs up catalog metadata, not original files or vault contents.

## Implementation and verified scope

The integration adapts the locally available **Disk Backup Manager 1.0.0** code at `D:\apps\disk_backup_manager`, corresponding to the repository requested by the user. That local project's README describes its source as private. The remote repository could not be independently retrieved, so this is not a claim of parity with a newer remote commit. Its original application is unchanged; the reused core is under `engine/disk_backup` with additional copy, cleanup and concurrency safeguards.

The local engine suite covers 37 original tests and 11 integration/safety tests. Native UI checks use disposable fixtures for planning, deduplication, confirmed cleanup, restore, search, indexing, history persistence, state movement/import and responsive layout. Real phone hardware transfer/deletion needs validation with the user's device; automated phone coverage uses mocks.

These filesystem and Windows MTP features require the standalone Windows application. A normal browser cannot provide equivalent unrestricted disk access. This addition does not implement Android/iOS native adapters. No intermediate server or runtime cloud service is introduced.
