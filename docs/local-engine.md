# Local engine contract

`vela_indexer.py` is deliberately separate from the user interface. The future
Tauri host will launch it at low priority, read its JSON summary, and store the
database and derivative cache under the user's application-data directory.

## Invariants

- Source files are read-only from Vela's perspective.
- A repeat scan only fingerprints files whose size or modification time changed.
- The catalog uses SQLite WAL mode so search remains available while indexing.
- Missing files are marked, never immediately deleted; this protects catalog
  history when removable media is unplugged.
- Cached thumbnails and previews must use a key derived from source identity,
  source fingerprint, derivative size, encoder settings, and renderer version.
- Face embedding work is queued after visual derivatives and runs entirely in
  the local `vela_vision.py` worker.

## Current developer command

```text
python engine/vela_indexer.py D:\Media --database D:\apps\media_app\.vela-data\catalog.sqlite
```

This is a developer-only starting point. The finished desktop app will invoke
the same engine without exposing a command line or local HTTP port.
