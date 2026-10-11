# ZeroCloud

Private, local-first media library. No runtime HTTP server or remote media service.

## Downloads and platform status

Get the Windows x64 preview installer from [GitHub Releases](https://github.com/Gnaneshwar-ux/ZeroCloud/releases). It bundles its local runtime; Node and Python are not required for installed users. The first preview is unsigned. See [release and packaging notes](docs/releases.md) for limitations and build instructions. Android/iOS are source scaffolds, not released native applications; no working APK is currently available.

## Launch

Double-click **Launch ZeroCloud.cmd** for the standalone desktop application. Existing Vela launchers remain compatible, and your established data location is preserved. **Launch Vela Browser.cmd** is the browser-only fallback, with restricted native-file capabilities.

The desktop app has portable SQLite catalog/cache storage and local DeepFace people indexing. The media workspace includes a disk/folder explorer, resumable disk scanning, optional portable cache per source folder, content-based image decoding and RAW rendering, pointer-anchored viewing, People corrections, indexing controls and offline GPS photo maps. See [the upgrade guide](docs/zerocloud-upgrade.md) for verified scope and codec/map limitations.

**Backup & protect** integrates the locally available Disk Backup Manager 1.0.0 engine in a separate, organized workspace: guided backups, verified content deduplication, pause/resume and history, safe restore, reviewed cleanup, search, disk/phone support, portable state and copied catalog import. See [the backup guide](docs/backup-workspace.md) for safety, migration and platform limitations. The original manager and existing catalogs are not automatically modified or imported.

**Library storage** selects, moves or opens a saved media data folder. Originals are linked in place; small thumbnails/face previews and model files are stored there. Backup vaults intentionally contain original files and are separate from thumbnail caches. Use **People** to start/pause indexing, name people and filter their photos.

For a fresh checkout, install Node/pnpm and Python 3.12+, then run **Setup Vela.cmd**. Initial package/model installation needs internet; photo analysis, viewing and backups run locally thereafter.

## Development and verification

- `pnpm build`: build the single-file browser UI used by both targets.
- `pnpm desktop:dev`: open the standalone local desktop window.
- `pnpm lint`, `pnpm verify:local`, `pnpm verify:desktop`: media verification.
- `python -m unittest discover -s tests`: backup engine and integration verification.
- `node scripts/verify-backup-ui.mjs <app-root> <fresh-test-folder>`: isolated native backup workflow test.
- `docs/portable-library.md`: media storage, backup/import, indexing and limitations.
- The earlier Tauri wrapper is retained in `src-tauri`; Rust compilation is currently blocked by this machine's application-control policy. The working Electron target does not change that policy.

Windows desktop and browser are currently implemented. Native Android/iOS adapters and universal codec support are not complete. Windows MTP support requires a working device driver and unlocked phone in file-transfer mode; physical phone transfer/deletion has not yet been validated on the user's device.
