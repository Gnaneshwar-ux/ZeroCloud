# ZeroCloud Memories

Open **Memories** under Explore to find suggested reels, create one, or play a saved memory.

- Groups use existing people, GPS clusters, and folder/date sessions. A suggestion needs at least four distinct media items.
- Reels contain up to 12 items, with three seconds per item: animated photos and short video excerpts, gentle fades, and locally composed ambient music.
- Output is a 720 × 1280 portrait MP4 with H.264 video and AAC audio. Originals are never overwritten; images are fitted with a background to retain their full composition.
- Video selection samples a few frames and chooses an excerpt using visual sharpness. It is not yet an AI understanding of the most meaningful moment.
- Trip dates currently use file modification dates, so copied or restored collections may need improved capture-date grouping later.

## Background work

Automatic creation is enabled by default. It waits for quiet browsing, low system CPU, and no active media viewing or face processing. The encoder runs below normal priority, with one encoding thread, and is suspended when the app becomes busy. Pause/resume controls are in Memories. The global Stop all indexing control also pauses this worker.

Automatic generation is limited to one reel per day. A quiet in-app notification appears at most once per day. Manual requests enter the same idle-only queue. ZeroCloud must be running for generation; this is not a separate always-on Windows service.

## Cache and portability

Completed reels are saved in `memories` inside the selected app library/cache folder. The catalog records their state and content-based identity; opening a saved reel does not regenerate it. The memory cache has a separate 512 MB budget and stops generating when full rather than deleting saved reels.

Carry the complete app library/cache folder—including `catalog.sqlite`, `vela-library.json`, and `memories`—to retain generated reels on another computer. Per-media-folder thumbnail exports do not currently export these shared multi-folder reels. Library-storage moves include the memories folder.

The desktop encoder is bundled through imageio-ffmpeg; psutil monitors CPU and pauses encoder work. There is no cloud service, upload, downloaded song, or online generation dependency. This first release does not yet provide commercial-song selection, editing templates, semantic highlights, or native mobile generation.
