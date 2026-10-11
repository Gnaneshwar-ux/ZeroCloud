import type { RuntimeMedia } from './runtimeMedia'
import { sourceVersion } from './mediaVersion'
import { desktop, mediaUrl } from './desktop'

export interface LocalFileHandle { kind: 'file'; name: string; getFile(): Promise<File> }
export interface LocalDirectoryHandle {
  kind: 'directory'; name: string
  values(): AsyncIterable<LocalFileHandle | LocalDirectoryHandle>
  isSameEntry(other: LocalDirectoryHandle): Promise<boolean>
  queryPermission(options: { mode: 'read' }): Promise<PermissionState>
  requestPermission(options: { mode: 'read' }): Promise<PermissionState>
}
export type FolderRecord = { id: string; name: string; addedAt: number; handle?: LocalDirectoryHandle; mode: 'linked' | 'copied' | 'session' | 'desktop'; count: number; location?: string; online?: boolean; cacheLocation?: string }
export type SourceRecord = { id: string; file?: File; handle?: LocalFileHandle }
export type SavedMedia = Omit<RuntimeMedia, 'thumbnailUrl' | 'fullUrl' | 'source'>
export type PreviewRecord = { key: string; mediaId: string; version: string; type: 'thumbnail' | 'full'; blob: Blob; duration?: string; accessed: number }
const DATABASE = 'vela-local-library-v1'
let database: Promise<IDBDatabase> | undefined
// Directory-upload files remain session-only. Never silently duplicate originals.
const sessionSources = new Map<string, File>()
const transientFull = new Map<string, PreviewRecord>()
const requestValue = <T>(request: IDBRequest<T>) => new Promise<T>((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error) })
const completion = (transaction: IDBTransaction) => new Promise<void>((resolve, reject) => { transaction.oncomplete = () => resolve(); transaction.onabort = () => reject(transaction.error ?? new Error('Local storage write was interrupted')); transaction.onerror = () => reject(transaction.error) })

function open() {
  if (!database) database = new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1)
    request.onupgradeneeded = () => {
      const db = request.result
      db.createObjectStore('folders', { keyPath: 'id' })
      db.createObjectStore('media', { keyPath: 'id' }).createIndex('folderId', 'folderId')
      db.createObjectStore('sources', { keyPath: 'id' })
      const previews = db.createObjectStore('previews', { keyPath: 'key' })
      previews.createIndex('mediaId', 'mediaId')
      previews.createIndex('accessed', 'accessed')
    }
    request.onsuccess = () => { request.result.onversionchange = () => { request.result.close(); database = undefined }; resolve(request.result) }
    request.onerror = () => { database = undefined; reject(request.error) }
    request.onblocked = () => reject(new Error('Close other ZeroCloud tabs to finish updating local storage.'))
  })
  return database
}

export async function loadLibrary(): Promise<{ folders: FolderRecord[]; items: RuntimeMedia[] }> {
  if (desktop) return desktop.request('load')
  const db = await open()
  const tx = db.transaction(['folders', 'media'], 'readonly')
  const [folders, media] = await Promise.all([requestValue(tx.objectStore('folders').getAll()), requestValue(tx.objectStore('media').getAll())])
  const updated: SavedMedia[] = []
  const items: RuntimeMedia[] = media.map((item: SavedMedia) => {
    const version = sourceVersion(item.byteSize, item.modified)
    const ready = item.version === version && item.thumbnailReady
    const record: SavedMedia = { ...item, version, date:item.captureDate??'Unknown capture date', thumbnailReady: ready, processing: item.kind === 'image' || item.kind === 'video' ? (ready ? 'ready' : 'queued') : 'unsupported', error: undefined }
    if (item.version !== version) updated.push(record)
    return record
  })
  if (updated.length) {
    const migration = db.transaction(['media', 'previews'], 'readwrite'); const done = completion(migration)
    for (const item of updated) {
      migration.objectStore('media').put(item)
      const cursor = migration.objectStore('previews').index('mediaId').openCursor(IDBKeyRange.only(item.id))
      cursor.onsuccess = () => { const row = cursor.result; if (row) { if (row.value.version !== item.version) row.delete(); row.continue() } }
    }
    await done
  }
  return { folders, items }
}

export async function saveFolder(folder: FolderRecord) {
  const db = await open(); const tx = db.transaction('folders', 'readwrite'); const done = completion(tx)
  tx.objectStore('folders').put(folder); await done
}

/** Atomic small batches keep metadata and its original reference together. */
export async function saveImportBatch(items: RuntimeMedia[], sources: SourceRecord[]) {
  const db = await open(); const tx = db.transaction(['media', 'sources', 'previews'], 'readwrite'); const done = completion(tx)
  for (const item of items) {
    const { source: _source, thumbnailUrl: _thumbnail, fullUrl: _full, ...record } = item
    tx.objectStore('media').put(record)
    // Invalidate old derivatives by version while preserving this file's current cache.
    const cursor = tx.objectStore('previews').index('mediaId').openCursor(IDBKeyRange.only(item.id))
    cursor.onsuccess = () => { const row = cursor.result; if (row) { if (row.value.version !== item.version) row.delete(); row.continue() } }
  }
  for (const source of sources) {
    if (source.file) sessionSources.set(source.id, source.file)
    tx.objectStore('sources').put({ id: source.id, handle: source.handle })
  }
  await done
}

export async function removeMissing(folderId: string, present: Set<string>) {
  const db = await open(); const tx = db.transaction(['media', 'sources', 'previews'], 'readwrite'); const done = completion(tx)
  const cursor = tx.objectStore('media').index('folderId').openCursor(IDBKeyRange.only(folderId))
  cursor.onsuccess = () => {
    const row = cursor.result
    if (!row) return
    if (!present.has(String(row.primaryKey))) {
      row.delete(); tx.objectStore('sources').delete(row.primaryKey)
      const previews = tx.objectStore('previews').index('mediaId').openCursor(IDBKeyRange.only(row.primaryKey))
      previews.onsuccess = () => { const preview = previews.result; if (preview) { preview.delete(); preview.continue() } }
    }
    row.continue()
  }
  await done
}

export async function saveFavorite(id: string, favorite: boolean) {
  if (desktop) { await desktop.request('favorite', { id, favorite }); return }
  const db = await open(); const tx = db.transaction('media', 'readwrite'); const done = completion(tx)
  const request = tx.objectStore('media').get(id)
  request.onsuccess = () => { if (request.result) tx.objectStore('media').put({ ...request.result, favorite }) }
  await done
}

export async function getSource(media: RuntimeMedia): Promise<File> {
  if (media.source) return media.source
  if (desktop) {
    const response = await fetch(mediaUrl(media.id))
    if (!response.ok) throw new Error(await response.text())
    return new File([await response.blob()], media.name, { lastModified: media.modified })
  }
  const session = sessionSources.get(media.id)
  if (session) return session
  const db = await open()
  const record: SourceRecord | undefined = await requestValue(db.transaction('sources').objectStore('sources').get(media.id))
  if (!record) throw new Error('Reconnect this folder to view its original.')
  try {
    const file = record.handle ? await record.handle.getFile() : record.file
    if (!file) throw new Error('Original file unavailable')
    if (file.size !== media.byteSize || file.lastModified !== media.modified) throw new Error('This file has changed. Rescan its folder to update the preview.')
    return file
  } catch (error) {
    if (error instanceof DOMException && error.name === 'NotAllowedError') throw new Error('Reconnect this folder to allow original-file access. Cached thumbnails are still available.')
    throw error
  }
}

export function previewKey(media: RuntimeMedia, type: PreviewRecord['type']) { return `${media.id}@${media.version}:${type}` }
export async function getPreview(media: RuntimeMedia, type: PreviewRecord['type']): Promise<PreviewRecord | undefined> {
  if (type === 'full') return transientFull.get(previewKey(media, type))
  if (desktop) {
    const result = await desktop.request<{ data: string; duration?: string } | null>('previewGet', { id: media.id, version: media.version, type })
    if (!result) return undefined
    const bytes = Uint8Array.from(atob(result.data), (letter) => letter.charCodeAt(0))
    return { key: previewKey(media, type), mediaId: media.id, version: media.version, type, blob: new Blob([bytes], { type: 'image/jpeg' }), duration: result.duration, accessed: Date.now() }
  }
  const db = await open(); const tx = db.transaction('previews', 'readwrite'); const done = completion(tx)
  const request = tx.objectStore('previews').get(previewKey(media, type))
  let result: PreviewRecord | undefined
  request.onsuccess = () => { result = request.result; if (result) tx.objectStore('previews').put({ ...result, accessed: Date.now() }) }
  await done; return result
}

let writes = 0
export async function savePreview(media: RuntimeMedia, type: PreviewRecord['type'], blob: Blob, duration?: string) {
  if (type === 'full') {
    transientFull.set(previewKey(media, type), { key: previewKey(media, type), mediaId: media.id, version: media.version, type, blob, accessed: Date.now() })
    let bytes = Array.from(transientFull.values()).reduce((sum, preview) => sum + preview.blob.size, 0)
    while (transientFull.size > 2 || bytes > 128 * 1024 * 1024) { const key = transientFull.keys().next().value!; bytes -= transientFull.get(key)!.blob.size; transientFull.delete(key) }
    return
  }
  if (desktop) {
    const bytes = new Uint8Array(await blob.arrayBuffer()); let binary = ''
    for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768))
    await desktop.request('previewSave', { id: media.id, version: media.version, data: btoa(binary), duration }); return
  }
  const db = await open(); const tx = db.transaction(['previews', 'media'], 'readwrite'); const done = completion(tx)
  tx.objectStore('previews').put({ key: previewKey(media, type), mediaId: media.id, version: media.version, type, blob, duration, accessed: Date.now() } satisfies PreviewRecord)
  if (type === 'thumbnail') {
    const request = tx.objectStore('media').get(media.id)
    request.onsuccess = () => {
      if (request.result?.version === media.version) tx.objectStore('media').put({ ...request.result, thumbnailReady: true, processing: 'ready', duration })
    }
  }
  await done
  if (++writes % 128 === 0) await trimPreviews()
}

/** Original references and the catalog are never evicted with derived images. */
async function trimPreviews() {
  const db = await open(); const tx = db.transaction(['previews', 'media'], 'readwrite'); const done = completion(tx)
  let bytes = 0; let fullBytes = 0; let fullCount = 0
  const cursor = tx.objectStore('previews').index('accessed').openCursor(null, 'prev')
  cursor.onsuccess = () => {
    const row = cursor.result; if (!row) return
    const record: PreviewRecord = row.value
    const size = record.blob.size
    if (bytes + size > 512 * 1024 * 1024 || (record.type === 'full' && (fullCount >= 8 || fullBytes + size > 256 * 1024 * 1024))) {
      row.delete()
      if (record.type === 'thumbnail') {
        const media = tx.objectStore('media').get(record.mediaId)
        media.onsuccess = () => { if (media.result?.version === record.version) tx.objectStore('media').put({ ...media.result, thumbnailReady: false, processing: 'queued' }) }
      }
    }
    else { bytes += size; if (record.type === 'full') { fullBytes += size; fullCount++ } }
    row.continue()
  }
  await done
}

export async function storageInfo() {
  if (desktop) { const result = await desktop.request<{ cacheBytes: number; databaseBytes: number; cacheLimit: number }>('storage'); return { persistent: true, used: result.cacheBytes + result.databaseBytes, quota: result.cacheLimit } }
  const estimate = await navigator.storage?.estimate?.()
  const persistent = await navigator.storage?.persisted?.()
  return { persistent: persistent ?? false, used: estimate?.usage ?? 0, quota: estimate?.quota ?? 0 }
}
export async function requestPersistentStorage() { return desktop ? true : navigator.storage?.persist?.() ?? false }
