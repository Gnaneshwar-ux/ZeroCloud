export type MediaKind = 'image' | 'raw' | 'video' | 'audio' | 'document' | 'other'
export type LibraryItem = { id: string; name: string; extension: string; kind: MediaKind; date: string; size: string; accent: string; previewUrl?: string }
const EXTENSIONS: Record<MediaKind, readonly string[]> = { image: ['.jpg', '.jpeg', '.jpe', '.jfif', '.png', '.apng', '.webp', '.gif', '.heic', '.heif', '.avif', '.tif', '.tiff', '.bmp', '.dib', '.ico', '.icns', '.psd', '.jp2', '.j2k', '.jpf', '.jpx', '.ppm', '.pgm', '.pbm', '.pnm', '.tga', '.pcx', '.qoi', '.sgi', '.rgb', '.rgba', '.svg', '.dds'], raw: ['.dng', '.cr2', '.cr3', '.crw', '.nef', '.nrw', '.arw', '.srf', '.sr2', '.raf', '.orf', '.rw2', '.raw', '.pef', '.srw', '.kdc', '.dcr', '.mos', '.mrw', '.3fr', '.fff', '.iiq', '.rwl', '.x3f'], video: ['.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm', '.3gp'], audio: ['.m4a', '.mp3', '.wav', '.flac', '.aac', '.ogg'], document: ['.pdf'], other: [] }
export function getMediaKind(extension: string): MediaKind { const normalized = extension.toLowerCase(); return (Object.keys(EXTENSIONS) as MediaKind[]).find((kind) => EXTENSIONS[kind].includes(normalized)) ?? 'other' }
export const DEFAULT_LIBRARY: LibraryItem[] = [
  { id: 'heic-walk', name: 'Morning walk', extension: 'HEIC', kind: 'image', date: 'Oct 8, 2026', size: '3.8 MB', accent: 'linear-gradient(145deg, #a9c6dd 0%, #e9d7c0 48%, #667b6c 100%)' },
  { id: 'mp4-drive', name: 'Coastal drive', extension: 'MP4', kind: 'video', date: 'Oct 6, 2026', size: '1.1 GB', accent: 'linear-gradient(145deg, #717d9d 0%, #9ab5d0 38%, #e3c39b 100%)' },
  { id: 'heic-market', name: 'Saturday market', extension: 'HEIC', kind: 'image', date: 'Oct 4, 2026', size: '4.1 MB', accent: 'linear-gradient(145deg, #c0d5b2 0%, #e9d19e 50%, #b47567 100%)' },
  { id: 'mov-rain', name: 'First rain', extension: 'MOV', kind: 'video', date: 'Sep 28, 2026', size: '682 MB', accent: 'linear-gradient(145deg, #5d7f90 0%, #a6c0ca 52%, #e0e7e2 100%)' },
  { id: 'dng-hills', name: 'Hillside', extension: 'DNG', kind: 'raw', date: 'Sep 22, 2026', size: '24.2 MB', accent: 'linear-gradient(145deg, #444f48 0%, #87976d 50%, #cab780 100%)' },
  { id: 'jpg-table', name: 'Dinner table', extension: 'JPG', kind: 'image', date: 'Sep 19, 2026', size: '2.9 MB', accent: 'linear-gradient(145deg, #6f5042 0%, #d5a871 48%, #f5ddaa 100%)' },
  { id: 'm4a-note', name: 'Voice note', extension: 'M4A', kind: 'audio', date: 'Sep 17, 2026', size: '8.3 MB', accent: 'linear-gradient(145deg, #aa8db2 0%, #d6c7e2 100%)' },
  { id: 'pdf-plan', name: 'Trip plan', extension: 'PDF', kind: 'document', date: 'Sep 12, 2026', size: '1.4 MB', accent: 'linear-gradient(145deg, #d6a6a6 0%, #f0d1c9 100%)' },
]
