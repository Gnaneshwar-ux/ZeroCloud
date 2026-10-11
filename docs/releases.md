# Release platforms and packaging

## Windows x64 preview

The installer bundles the Electron UI, Python engine, local face models and image/video helpers. It requires no separately installed Node or Python runtime. Media folders, portable caches, backup vaults and personal catalogs are not bundled. The installer does not remove saved catalogs on uninstall.

This first preview is unsigned. No signing certificate or Android release key was provided. Windows may show publisher/reputation warnings; managed Application Control can prohibit unsigned helpers. The app does not weaken Windows security policies. HEIC requires either a permitted pillow-heif decoder or an installed Windows HEIF codec; unsupported files show an error, not a fabricated preview.

Build from a configured source checkout:

```powershell
pnpm install
pnpm build
node scripts/package-desktop.mjs release/win-unpacked
pnpm release:windows --prepackaged release/win-unpacked --publish never
```

Python dependencies must first be installed into `.venv` using `engine/requirements.txt`. The face model seed is bundled under `models/.deepface/weights`. Dependency licenses remain with their installed packages, with additional model/dataset notices in `third-party`.

## Android and iOS

The responsive UI and Tauri scaffold are included as source only. Native MediaStore/Storage Access Framework access, persisted URI permissions, background workers, local database/codec/face recognition bridges, sharing/deletion and device testing are still required. The Windows Python/Electron engine cannot run unchanged inside an APK. No functional Android APK is included in this Windows preview, and the empty scaffold must not be represented as feature-complete Android support.

## Privacy and backups

Only application code, public model assets and dependencies are distributed. Before publishing, exclude databases, `.vela-*` data, `.venv`, cache folders, browser profiles, workspace JSON files and personal originals. Back up your existing catalog before installing a preview. Originals are linked, not copied into the app; backup vaults are separate user-chosen folders.
