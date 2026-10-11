# Capture-date timeline

Library now contains photos and videos together. The separate Photos and Videos sidebar shortcuts were removed; use the Images/RAW/Videos buttons inside Library instead.

Filter by capture year, an inclusive From/To date range, folder, media type, search text, and newest/oldest capture order. Choose **Unknown capture date** to review files without usable camera metadata. **Clear filters** resets the range, year, media type, folder and search.

The gallery has day headings and a floating date indicator while scrolling. Its rows remain virtualized, and recent thumbnail URLs are reused.

## Date sources

- Photos: EXIF DateTimeOriginal, followed by DateTimeDigitized when available. Pillow/HEIF and ExifRead cover the supported metadata containers; a file without a usable capture tag remains undated.
- Videos: embedded creation metadata read locally by FFmpeg. This is not the filesystem creation or modification date. Edited/transcoded videos can carry their export date in this metadata; the original shooting date cannot always be recovered.
- Filesystem modification times and EXIF modification-only tags are not used as substitutes for capture dates.
- Camera-local dates are retained when present. Missing timezone information is not invented.

The desktop app indexes these dates in the background, saves them in the catalog, and includes them in portable folder-cache exports. No originals or EXIF tags are modified. Stop all indexing and viewer/scroll priority also pause this worker. The Library displays date-index progress until it finishes; Settings → Indexing & activity shows the counts.

New trip-memory suggestions use indexed capture dates rather than copied-file timestamps. Existing generated reels are retained. Browser-only collections without previously saved capture metadata display the undated group; the new native metadata reader currently runs in the desktop app.
