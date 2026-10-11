# ZeroCloud: local media, no cloud

Use `Launch ZeroCloud.cmd` in the source folder or `ZeroCloud.exe` in the Windows package. Previous Vela launchers and the established `%LOCALAPPDATA%\Vela\Library` data location remain compatible; the rename does not reset saved data.

## Viewing and formats

The viewer fits the entire image, uses pointer-anchored scroll/pinch zoom, and accumulates drag events once per display frame. Double-click increases the displayed zoom by five percentage points at the clicked spot. Fit and 100% are separate controls. Originals are never overwritten.

Desktop decoding uses Pillow, pillow-heif/libheif, and rawpy/LibRaw. Contents are detected independently of filename extensions. JPEG/PNG/WebP/GIF/BMP/AVIF, HEIC/HEIF, TIFF, JPEG2000, PSD composite images, PNM, PCX, TGA, QOI, icons, and many camera RAW formats are recognized. Actual support depends on codec, camera, compression, and file validity; this is not a promise to decode every format. RAW is rendered with camera white balance for display, not changed on disk. Animated browser-native images remain animated; fallback conversions show the first frame. Multi-page image navigation and HDR/16-bit display are not yet implemented.

Full-resolution fallback conversions are memory-only and reuse a bounded recent-image cache. Persistent thumbnails are at most 720 pixels. A zero-filled file is corrupt, not a missing-decoder problem: restore its healthy original from backup.

Videos play the original bitstream with byte-range streaming, rather than copying or re-encoding the original. Small posters are retained when the embedded browser supports the source codec. Unsupported video codecs still need a compatible playback/transcoding adapter; this release does not guarantee every codec or lag-free playback on every device.

## People corrections

DeepFace SFace recognition uses YuNet landmark-based detection at a conservative confidence threshold. Refinding rechecks photos without deleting names or correction vectors. Select duplicate groups, choose Same face, select the retained group/name, and confirm. Multi-pose reference examples and bounded per-person calibration guide future matching. Review for accuracy provides continuous Yes/No checks: the UI advances from a preloaded queue, answers are persisted, and grouping runs behind the review. Interrupted queued work resumes after restart. Different-person decisions and faces appearing together constrain automatic merges. Select objects/false groups and choose Not a face to remember rejected boxes for unchanged photos. Recognition remains approximate; confirm only groups you know are the same person.

Back to People and browser Back return from a person's filtered photos to the saved People list and its previous scroll position.

## Disk explorer and indexing

Disks & folders lists mounted drives and lets you browse or enter an absolute folder path. Index a whole disk or selected folder. Disk scanning is asynchronous and commits metadata in batches. Stop all indexing pauses disk scans, thumbnail generation, faces, and GPS; existing previews and on-demand viewing remain available. An already running item may finish. Resume continues saved work. An unfinished disk scan survives app restart, and resumes a catalog-preserving walk; it may walk already scanned paths again without duplicating metadata or originals.

App data, per-folder caches, linked child roots, Windows system folders at a scan root, and symbolic links are excluded. Inaccessible folders are reported; incomplete scans do not mark unverified old entries as missing. Overlapping linked roots are not indexed twice.

## Portable cache per folder

When adding media, choose App storage, On this drive, or Choose cache folder. On this drive creates `.vela-cache` within the source folder. A separate cache folder must be empty, or the existing matching portable folder cache. Existing folders can change this with Folders > Portable cache.

Each cache contains `vela-folder-cache.json`, a snapshot `catalog.sqlite`, and small thumbnails/face crops. It carries that source folder's stable IDs, names, confirmed vectors, false-face corrections, favorites, and GPS. It does not contain original copies or persistent full-size previews. Updates sync in batches and on normal close. Use Sync portable caches, stop indexing, and close the app before safely removing a drive. A sync failure is reported and retried; no silent successful-backup claim is made.

On another computer add the original media folder, choose its existing portable cache, and keep the same relative layout or relink. A cache inside the media folder follows changed drive letters. The main Library storage options still manage a complete combined library; selecting the current location is a harmless no-op. Do not nest a migrated complete data folder within itself.

## Places and privacy

A separate lightweight worker reads actual EXIF GPS alongside face indexing. Photos without GPS are not assigned guessed positions. Places uses Leaflet with bundled Natural Earth country/coastline data, offline markers, nearby photo groups, and filtering. No coordinates or photos are uploaded. The offline base map does not include street-level tiles or reverse-geocoded city names. Detailed offline street maps would need a separately supplied map dataset/provider and licensing arrangement. No Google Maps service is used.

Counters are read from one SQLite snapshot and include failures separately; failed images are checked but are not successfully recognized. Stop/resume persists across refresh and restart.

## Platform scope

The standalone Windows package and browser fallback are verified. Android and iOS have matching generated icons and a shared responsive UI scaffold, but native storage, decoder and local face-engine integration are not yet completed or released on those platforms.

## Verification

The upgrade regression scripts exercise real misnamed JPEG/HEIC and genuine HEIC, zero-filled corruption, generated image formats, RAW decoding, EXIF GPS, confirmed merge/rejection persistence, fresh-machine portable cache import, full-image fit, pointer anchoring, burst dragging, navigation, and global pause/resume. Face recognition is tested on linked real photos in an isolated catalog. Tests never reset the personal media library.

Open-source components: https://github.com/serengil/deepface, https://github.com/opencv/opencv_zoo, https://pillow.readthedocs.io/, https://github.com/strukturag/libheif, https://github.com/letmaik/rawpy, https://leafletjs.com/, https://github.com/topojson/world-atlas.
