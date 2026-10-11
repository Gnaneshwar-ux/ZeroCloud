# Portable local library

## Run

`Launch Vela.cmd` now starts the standalone Electron desktop window when its
runtime and local Python environment are installed. `Launch Vela Desktop.cmd`
starts it explicitly. `Launch Vela Browser.cmd` opens the standalone browser
fallback. Neither release needs a local HTTP server or a remote service.

The desktop UI communicates with a Python subprocess through JSON over standard
input/output, not network sockets. Renderer Node access is disabled; preload
exposes a narrow allowlisted IPC surface. Original and face resources are looked
up by catalog IDs through a secure, CORS-enabled local scheme. File paths supplied
by the renderer cannot directly open arbitrary files. Original videos support
byte-range streaming; the original bitstream is not copied or re-encoded.

The generated Windows x64 folder build is under `release/Vela-win-x64-20261010`.
Copy the **entire folder**, then run `Vela.exe`; Python/Node installation is not
required. This build includes the public face-model weights for offline first use.
The app and media-data folder are separate: use **Open existing library** to import
your saved catalog/cache. The build contains no personal catalog or originals.
`pnpm desktop:package` builds another runtime folder; set `VELA_MODEL_SEED` to an
existing Vela model directory to bundle its three public weights.

## Storage and transfer

The default folder is `%LOCALAPPDATA%/Vela/Library` (Vela's local application data),
not the user's Documents folder, which can be redirected to OneDrive. In **Library storage**:

- **Choose new data folder** starts a separate empty library and retains the old
  library. The selected folder must be empty or already a valid Vela library.
- **Move library data folder** copies the current library into an empty folder,
  rebases root mappings, and switches to it. The old folder is retained as a backup.
  This operation copies only the catalog, derivatives, settings, and model files,
  never originals.
- **Open existing library** uses a previously saved folder in place. This is the
  import operation on another computer; it does not duplicate the imported data.
- **Prepare for backup** pauses face work and checkpoints SQLite. Close Vela
  before copying the complete data folder. Back up originals separately.
- The thumbnail budget can be 128, 256, 512 (default), or 1024 MiB. LRU eviction
  affects thumbnails only, not faces, favorites, or originals.

Data folder layout:

```
vela-library.json         format/version/library identity
catalog.sqlite           folders, media metadata, favorites, face vectors/names
thumbnails/*.jpg         720px maximum, compressed derivatives
faces/*.jpg              160px maximum, compressed face previews
models/.deepface/weights  reusable local model files (about 40 MB)
```

SQLite WAL sidecars can exist while Vela is open. The engine commits changes and
closes connections, and checkpoints on orderly shutdown. Never copy just the
SQLite file while the app is writing it.

Media IDs are folder UUID + relative path. Originals are resolved using a relative
root when possible, then the previous absolute root. On a new machine, retain the
same relative layout, or **Relink folder** to preserve IDs, names, favorites, and
thumbnails. A timestamp-only change with the same size and first/last 16KiB sample
fingerprint preserves the old derivative version after rescan. That lightweight
fingerprint is a rescan heuristic, not cryptographic proof of entire-file equality.
Only the scanned root is reconciled, and incomplete scans retain unverified files.
Original writes/deletion are never performed by the engine.

Full HEIC viewing decodes source dimensions to a PNG in memory. Full-resolution
derivatives are not saved in this portable folder. Only a few full-view memory
URLs/Blobs are retained. Unsupported RAW/TIFF variants and video codecs can still
require additional decoder work; their original data remains untouched.

## People

Open **People → Start face indexing**. DeepFace 0.0.102 uses the SFace ONNX
recognizer with OpenCV detection, true face detection enforcement, 1600px analysis
input, and a conservative cosine-distance threshold. Model inference uses two CPU
threads and the worker runs at below-normal priority. Visible/nearby items can be
reprioritized; opening the viewer pauses starting new face jobs (the current
inference may finish). The decoder queue also pauses background preview work.

Photos, including HEIC, are processed locally. The first model use downloads model
weights; no photos are uploaded. The worker stores normalized vectors and small
face crops, groups suggested matches, persists progress, and resumes if enabled
on restart. No-face photos produce no fake person. Failed files are counted and
can be retried via the indexing control. Videos are not face-indexed in this version.

Click a face for its photos, click **Add a name** to name it, and use the naming
dialog's merge section for multiple groups of the same person. Automated grouping
is imperfect and is not an assertion of identity. Progress appears in the main
library as well as People. GPS/Places remains unimplemented and is labeled so.

## Browser fallback

The browser retains metadata, favorites, and small previews in IndexedDB. Modern
secure origins can persist folder handles, subject to renewed browser permission.
In standalone file mode, uploaded originals are session-only and must be
reconnected after refresh; **new originals are no longer copied into IndexedDB**.
Old browser File copies are retained for backwards compatibility, not deleted.
Full decoded HEIC previews are now memory-only. Browser data and desktop library
data are separate; automatically migrating a personal browser profile is not done.
Use the desktop app for automatic original access after restart and portable folders.

## Verification

- `pnpm build` / `pnpm lint`
- `pnpm verify:desktop <project> <isolated-work-directory>`
- `pnpm verify:local <project> <isolated-work-directory>`
- `node scripts/verify-faces.mjs <project> <isolated-work-directory> <real-photo-folder>`
- `node scripts/verify-native-media.mjs <project> <isolated-work-directory> <test-webm> <saved-face-test-folder>`

Tests use isolated profiles and fixtures; no personal Chrome profile is controlled.
The hidden native test window explicitly emulates foreground visibility because
the app intentionally suspends decoder work while hidden.

Windows desktop and browser are working targets. Mobile (Android/iOS) packaging
and a native mobile filesystem/vision adapter remain future work; the shared
React UI and narrow bridge make that possible but do not constitute tested mobile apps.
