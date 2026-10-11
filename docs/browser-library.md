# Browser library

Use `Launch Vela Browser.cmd` to open the single-file browser build with no
HTTP server. See [portable-library.md](portable-library.md) for desktop storage,
face indexing, backup/import, work scheduling, and current limitations.

Browser metadata, favorites and small thumbnails remain in IndexedDB
`vela-local-library-v1`. Saved folder handles work on supported secure origins,
subject to renewed browser permission. Folder uploads in standalone file mode now
keep originals in session memory only, not large stored copies. Reconnect the
same folder after refresh to open originals; cached thumbnails and favorites
remain. Old browser copies are retained for compatibility and not deleted.

HEIC full-resolution PNGs are memory-only, not persisted. Small 720px JPEG
thumbnails are versioned and cached. The queue remains one decoder, with open
media > visible > nearby > background, cancellation/resume, and virtualization.
The viewer fits the entire image, supports 100% pixels, wheel/pinch zoom, drag pan,
double-click fit, and fullscreen. Video plays the original stream where its codec
is supported. Use the desktop app for a movable data folder, automatic original
access after restart, and actual on-device DeepFace people indexing.

`pnpm verify:local` uses an isolated browser profile to test these behaviors,
including a real HEIC and a 1,205-item catalog. Its folder-handle test uses an
ephemeral loopback page solely to test browser APIs restricted under file URLs;
the test server shuts down and is not an application runtime requirement.
