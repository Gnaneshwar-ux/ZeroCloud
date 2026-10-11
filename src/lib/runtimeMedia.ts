import { decodeHeic } from './heicDecoder'
import { getMediaKind, type LibraryItem } from './catalog'
import { getPreview, getSource, savePreview, type PreviewRecord } from './libraryStore'
import { sourceVersion } from './mediaVersion'
import { desktop, mediaUrl } from './desktop'

export type RuntimeMedia = LibraryItem & {
  folderId: string; relativePath: string; byteSize: number; modified: number; version: string
  source?: File; thumbnailUrl?: string; fullUrl?: string; thumbnailReady?: boolean
  processing: 'queued' | 'processing' | 'ready' | 'failed' | 'unsupported'
  favorite?: boolean; error?: string; duration?: string
  capturedAt?: string | null; captureDate?: string | null; dateSource?: string
  categories?: string[]
  categorySource?: string
}
const fullUrls = new Map<string, string>()
const fullSizes = new Map<string, number>()
const fullJobs = new Map<string, {promise:Promise<string>;background:boolean}>()
const fullPins = new Map<string,number>()
export function pinOriginal(media:RuntimeMedia) {
  const key=`${media.id}@${media.version}`
  fullPins.set(key,(fullPins.get(key)||0)+1)
  return ()=>{const count=(fullPins.get(key)||1)-1;if(count)fullPins.set(key,count);else fullPins.delete(key);trimFullUrls()}
}
const needsDecoder = (media:RuntimeMedia) => media.kind==='raw'||['HEIC','HEIF','TIF','TIFF','PSD','JP2','J2K','JXL'].includes(media.extension)
export function initialOriginalUrl(media:RuntimeMedia) {
  return fullUrls.get(`${media.id}@${media.version}`)||(desktop&&media.folderId!=='memories'&&!needsDecoder(media)?mediaUrl(media.id):'')
}
const abort = (signal?: AbortSignal) => { if (signal?.aborted) throw new DOMException('Work paused', 'AbortError') }
export function createRuntimeMedia(file: File, index: number, folderId: string, relativePath: string): RuntimeMedia {
  const extension = file.name.includes('.') ? `.${file.name.split('.').pop()!.toLowerCase()}` : ''
  const kind = getMediaKind(extension)
  return { id: `${folderId}/${relativePath}`, folderId, relativePath, byteSize: file.size, modified: file.lastModified,
    version: sourceVersion(file.size, file.lastModified), name: file.name, extension: extension.slice(1).toUpperCase() || 'FILE', kind,
    date: 'Unknown capture date',
    size: formatBytes(file.size), accent: ACCENTS[index % ACCENTS.length],
    processing: kind === 'image' || kind === 'video' ? 'queued' : 'unsupported' }
}

/** Background work only creates small thumbnails; originals remain lazy. */
export async function makeThumbnail(media: RuntimeMedia, signal?: AbortSignal): Promise<PreviewRecord> {
  abort(signal)
  const cached = await getPreview(media, 'thumbnail')
  if (cached) return cached
  abort(signal)
  if (desktop && (media.kind === 'raw' || ['HEIC', 'HEIF', 'TIF', 'TIFF', 'PSD', 'JP2', 'J2K', 'JXL'].includes(media.extension))) {
    const blob = await nativeImage(media, true); abort(signal)
    await savePreview(media, 'thumbnail', blob)
    return { key: `${media.id}@${media.version}:thumbnail`, mediaId: media.id, version: media.version, type: 'thumbnail', blob, accessed: Date.now() }
  }
  const original = desktop && media.kind === 'video' ? mediaUrl(media.id) : await getSource(media)
  abort(signal)
  let blob: Blob; let duration: string | undefined
  if (media.kind === 'video') {
    const result = await captureVideoPoster(original, signal)
    blob = result.blob; duration = result.duration
  } else if (media.kind === 'image') {
    if ((media.extension === 'HEIC' || media.extension === 'HEIF') && await isHeif(original as File)) {
      blob = await decodeHeic(original as File, false, signal)
    } else {
      try { blob = await resizeImage(original as File, signal) }
      catch (error) { abort(signal); if (!desktop) throw error; blob = await nativeImage(media, true); abort(signal) }
    }
  } else throw new Error('This format needs the desktop media decoder.')
  abort(signal)
  await savePreview(media, 'thumbnail', blob, duration)
  return { key: `${media.id}@${media.version}:thumbnail`, mediaId: media.id, version: media.version, type: 'thumbnail', blob, duration, accessed: Date.now() }
}

/** Video and browser-native images open immediately without a poster dependency. */
export function openOriginal(media: RuntimeMedia, signal?: AbortSignal, background=false): Promise<string> {
  const key=`${media.id}@${media.version}`
  const existing=fullUrls.get(key)
  if(existing){fullUrls.delete(key);fullUrls.set(key,existing);return Promise.resolve(existing)}
  if(desktop&&!needsDecoder(media))return Promise.resolve(mediaUrl(media.id))
  const pending=fullJobs.get(key)
  if(pending&&(background||!pending.background||!desktop))return pending.promise
  // A click promotes speculative work into the dedicated foreground lane.
  // It must not inherit a promise queued behind an unrelated thumbnail decode.
  const job=loadOriginal(media,signal,background).finally(()=>{if(fullJobs.get(key)?.promise===job)fullJobs.delete(key)})
  fullJobs.set(key,{promise:job,background});return job
}
async function loadOriginal(media: RuntimeMedia, signal?: AbortSignal, background=false): Promise<string> {
  const key = `${media.id}@${media.version}`
  const existing = fullUrls.get(key)
  if (existing) { fullUrls.delete(key); fullUrls.set(key, existing); return existing }
  if (desktop && (media.kind === 'raw' || ['HEIC', 'HEIF', 'TIF', 'TIFF', 'PSD', 'JP2', 'J2K', 'JXL'].includes(media.extension))) {
    const url = await nativeViewUrl(media,background); abort(signal)
    return url
  }
  if (desktop && !['HEIC', 'HEIF'].includes(media.extension)) return mediaUrl(media.id)
  const heic = media.extension === 'HEIC' || media.extension === 'HEIF'
  const cached = heic ? await getPreview(media, 'full') : undefined
  abort(signal)
  let original: Blob = cached?.blob ?? await getSource(media)
  abort(signal)
  if (heic && !cached && await isHeif(original)) {
    original = await decodeHeic(original, true, signal)
    abort(signal)
    // Full preview caching is optional; quota issues must not prevent viewing.
    try { await savePreview(media, 'full', original) } catch { /* Original references remain saved. */ }
  }
  abort(signal)
  return retainFullUrl(key, original)
}

function retainFullUrl(key: string, original: Blob) {
  const url = URL.createObjectURL(original)
  const previous = fullUrls.get(key); if (previous) URL.revokeObjectURL(previous)
  fullUrls.set(key, url)
  fullSizes.set(key,original.size)
  trimFullUrls()
  return url
}
function trimFullUrls(){
  while(fullUrls.size>3||(fullUrls.size>1&&[...fullSizes.values()].reduce((total,size)=>total+size,0)>128*1024*1024)){
    const oldest=[...fullUrls.keys()].find(key=>!fullPins.has(key));if(!oldest)break
    URL.revokeObjectURL(fullUrls.get(oldest)!);fullUrls.delete(oldest);fullSizes.delete(oldest)
  }
}

async function nativeImage(media: RuntimeMedia, thumbnail: boolean, background=false): Promise<Blob> {
  const decoded = await desktop!.request<{data: string; mime: string}>('decodeImage', { id: media.id, thumbnail, foreground:!thumbnail&&!background })
  // Avoid the per-character iterator/callback allocation on multi-megabyte images.
  const binary=atob(decoded.data),bytes=new Uint8Array(binary.length)
  for(let index=0;index<binary.length;index++)bytes[index]=binary.charCodeAt(index)
  return new Blob([bytes], {type: decoded.mime})
}
async function nativeViewUrl(media:RuntimeMedia,background=false):Promise<string>{
  const decoded=await desktop!.request<{url:string}>('decodeImage',{id:media.id,stream:true,foreground:!background})
  return decoded.url
}

async function isHeif(blob: Blob) {
  const header = new Uint8Array(await blob.slice(0,64).arrayBuffer())
  return String.fromCharCode(...header.slice(4,8)) === 'ftyp'
}

/** Only invoked after browser decoding fails. No full-size derivative is saved. */
export async function recoverImage(media: RuntimeMedia): Promise<string> {
  if (desktop) return nativeViewUrl(media)
  const original = await getSource(media)
  const reader = original.stream().getReader()
  let allZero = true
  try { while (true) { const {done, value} = await reader.read(); if (done) break; if (value.some((byte) => byte !== 0)) { allZero = false; break } } }
  finally { await reader.cancel(); reader.releaseLock() }
  if (allZero) throw new Error('This file contains only zero bytes, not image data. Restore a healthy copy from your backup; installing a decoder cannot repair it.')
  throw new Error('This image could not be decoded by the browser. Try the desktop app for additional local decoders, or check the original file.')
}

async function resizeImage(blob: Blob, signal?: AbortSignal): Promise<Blob> {
  const url = URL.createObjectURL(blob)
  const image = new Image(); image.decoding = 'async'
  try {
    const ready = waitFor(image, 'load', signal, 30000)
    image.src = url; await ready; abort(signal)
    const scale = Math.min(1, 720 / Math.max(image.naturalWidth, image.naturalHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale)); canvas.height = Math.max(1, Math.round(image.naturalHeight * scale))
    const context = canvas.getContext('2d'); if (!context) throw new Error('Image renderer unavailable')
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    return await canvasBlob(canvas)
  } finally { image.removeAttribute('src'); URL.revokeObjectURL(url) }
}

async function captureVideoPoster(original: File | string, signal?: AbortSignal): Promise<{ blob: Blob; duration?: string }> {
  const url = typeof original === 'string' ? original : URL.createObjectURL(original)
  const video = document.createElement('video'); video.preload = 'auto'; video.muted = true; video.playsInline = true
  if (typeof original === 'string') video.crossOrigin = 'anonymous'
  try {
    const metadata = waitFor(video, 'loadedmetadata', signal, 15000)
    video.src = url; video.load(); await metadata; abort(signal)
    if (!video.videoWidth || !video.videoHeight) throw new Error('No video stream found; this file may use an unsupported codec.')
    const seconds = Number.isFinite(video.duration) ? Math.round(video.duration) : 0
    const target = Math.min(1, Math.max(0, video.duration / 10))
    if (target > 0) { const seek = waitFor(video, 'seeked', signal, 15000); video.currentTime = target; await seek }
    else if (video.readyState < 2) await waitFor(video, 'loadeddata', signal, 15000)
    abort(signal)
    const scale = Math.min(1, 720 / Math.max(video.videoWidth, video.videoHeight))
    const canvas = document.createElement('canvas'); canvas.width = Math.max(1, Math.round(video.videoWidth * scale)); canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
    const context = canvas.getContext('2d'); if (!context) throw new Error('Video poster renderer unavailable')
    context.drawImage(video, 0, 0, canvas.width, canvas.height)
    return { blob: await canvasBlob(canvas), duration: `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` }
  } finally { video.pause(); video.removeAttribute('src'); video.load(); if (typeof original !== 'string') URL.revokeObjectURL(url) }
}

function canvasBlob(canvas: HTMLCanvasElement) { return new Promise<Blob>((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('Could not create preview')), 'image/jpeg', 0.82)) }
function waitFor(target: EventTarget, event: string, signal?: AbortSignal, timeout = 15000) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Work paused', 'AbortError')); return }
    const cleanup = () => { clearTimeout(timer); target.removeEventListener(event, ready); target.removeEventListener('error', error); signal?.removeEventListener('abort', cancelled) }
    const ready = () => { cleanup(); resolve() }
    const error = () => { cleanup(); reject(new Error('This browser cannot decode this image or video codec.')) }
    const cancelled = () => { cleanup(); reject(new DOMException('Work paused', 'AbortError')) }
    const timer = setTimeout(() => { cleanup(); reject(new Error('Preview timed out. You can still try opening the original.')) }, timeout)
    target.addEventListener(event, ready, { once: true }); target.addEventListener('error', error, { once: true }); signal?.addEventListener('abort', cancelled, { once: true })
  })
}
export function formatBytes(size: number) {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`
}
const ACCENTS = ['linear-gradient(135deg,#d8d4ff,#8d96dc)', 'linear-gradient(135deg,#ffe0bc,#e99679)', 'linear-gradient(135deg,#b5e3d2,#5f9e91)', 'linear-gradient(135deg,#c6d8f6,#718ec5)']
