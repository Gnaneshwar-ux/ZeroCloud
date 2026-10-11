import type { RuntimeMedia } from './runtimeMedia'
// Only adjacent sources, never autoplay or fetch whole videos. Detached media
// elements and their decoded surfaces are released as soon as navigation changes.
export function prefetchViewerNeighbors(items: RuntimeMedia[], open: (item: RuntimeMedia, signal?: AbortSignal) => Promise<string>) {
 let active = true
 const abort = new AbortController()
 const images: HTMLImageElement[] = [], videos: HTMLVideoElement[] = []
 const timer = setTimeout(() => { void (async () => {
   for (const item of items.slice(0,2)) {
    if (!active) break
    try {
     const url = await open(item, abort.signal)
     if (!active) break
     if (item.kind === 'video') { const player = document.createElement('video'); player.preload = 'metadata'; player.muted = true; videos.push(player); player.src = url }
     else { const image = new Image(); image.decoding = 'async'; images.push(image); image.src = url; await image.decode().catch(() => {}) }
    } catch { /* A missing neighbor must never interrupt the open original. */ }
   }
 })() }, 350)
 return () => { active = false; abort.abort(); clearTimeout(timer); for (const image of images) image.removeAttribute('src'); for (const player of videos) { player.pause(); player.removeAttribute('src'); player.load() } }
}
