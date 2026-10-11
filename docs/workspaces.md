# Save and reopen ZeroCloud workspaces

Open **Library storage → Saved workspaces**.

**Save workspace** writes a small `.zerocloud-workspace.json` file containing the library identity/location, saved original-folder paths, per-folder portable cache locations, and the backup-state location if one exists. Library settings, people names, favorites, indexing records and cache references remain in the linked catalog. This is not an embedded database, a backup, or a historical rollback snapshot: later catalog updates remain available.

**Open workspace** selects that file, validates its format and matching library identity, and loads the existing catalog without rescanning originals. It restores the saved folder/cache paths. The current library is not deleted. If the catalog cannot be found, choose its matching saved data folder. Missing originals remain listed; use **Folders → Relink folder** when their layout has changed. Missing caches need to be reconnected. Backup jobs must be paused before changing workspaces; opening a backup catalog never resumes deletion automatically.

For an external disk, keep the workspace file near the catalog, originals and portable caches. References include relative paths as well as the previous absolute location, so the same layout can move between drive letters. Move/copy the actual catalog and cache folders too. The JSON file alone does not contain thumbnails, face vectors or originals. There is no OS file association in the current package: use the app's Open workspace button rather than double-clicking the JSON file.

Keep your previous library and any migration backups until you have verified the new workspace. Personal catalogs, workspace files and source media are not included in this repository or release.

Verification covers native Save/Open buttons, loading from a different profile, stable IDs/favorites/cached previews, per-folder cache and backup-state paths, relative path relocation, offline originals, rejection of unsupported workspace files, and no new folder scan during loading. These native workspace capabilities require the Windows standalone application.
