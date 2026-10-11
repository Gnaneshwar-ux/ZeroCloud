import { startTransition, useCallback, useEffect, useRef, useState } from 'react'
import { createRuntimeMedia, makeThumbnail, openOriginal, type RuntimeMedia } from './runtimeMedia'
import { getPreview, loadLibrary, removeMissing, requestPersistentStorage, saveFavorite, saveFolder, saveImportBatch, storageInfo, type FolderRecord, type LocalDirectoryHandle, type LocalFileHandle, type PreviewRecord, type SourceRecord } from './libraryStore'
import { PriorityQueue, type QueueStatus } from './priorityQueue'
import { desktop } from './desktop'

type ImportEntry = { file: File; path: string; handle?: LocalFileHandle }
export function useLibrary() {
  const [items, setItems] = useState<RuntimeMedia[]>([])
  const [folders, setFolders] = useState<FolderRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [notice, setNotice] = useState('')
  const [scan, setScan] = useState('')
  const [captureProgress,setCaptureProgress]=useState<{done:number;total:number;dated:number;state:string}|null>(null)
  const [categoryProgress,setCategoryProgress]=useState<{done:number;total:number;state:string}|null>(null)
  const [queueState, setQueueState] = useState<QueueStatus>({ pending: 0, current: '', paused: false })
  const [storage, setStorage] = useState({ persistent: false, used: 0, quota: 0 })
  const [queue] = useState(() => new PriorityQueue())
  const scanBusy = useRef(false)
  const records = useRef(new Map<string, RuntimeMedia>())
  const folderRecords = useRef<FolderRecord[]>([])
  const scheduled = useRef(new Set<string>())
  const viewing = useRef(false)
  const scrolling = useRef(false)
  const [indexingStopped, setIndexingStopped] = useState(() => { try { return localStorage.getItem('vela-indexing-stopped') === 'true' } catch { return false } })
  const stopped = useRef(indexingStopped)
  const facePriority = useRef(new Map<string, number>())
  const facePriorityTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const changes = useRef(new Map<string, { version?: string; patch: Partial<RuntimeMedia> }>())
  const flushTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const mergePatch = useCallback((id: string, patch: Partial<RuntimeMedia>, version?: string) => {
    const existing = changes.current.get(id)
    changes.current.set(id, { version, patch: { ...(existing && existing.version === version ? existing.patch : {}), ...patch } })
    const flush = () => {
      if (scrolling.current) { flushTimer.current = setTimeout(flush, 100); return }
      const batch = new Map(changes.current); changes.current.clear(); flushTimer.current = undefined
      startTransition(() => setItems((old) => old.map((item) => {
        const change = batch.get(item.id); if (!change || (change.version && item.version !== change.version)) return item
        const updated = { ...item, ...change.patch }; records.current.set(item.id, updated); return updated
      })))
    }
    if (flushTimer.current === undefined) flushTimer.current = setTimeout(flush, 120)
  }, [])

  useEffect(() => {
    let timer:ReturnType<typeof setTimeout> | undefined
    const idle=()=>{scrolling.current=false;queue.scrollBusy(false);if(desktop)void desktop.request('faceControl',{paused:viewing.current}).catch(()=>{})}
    const scroll=()=>{
      if(!scrolling.current){scrolling.current=true;queue.scrollBusy(true);if(desktop)void desktop.request('faceControl',{paused:true}).catch(()=>{})}
      clearTimeout(timer);timer=setTimeout(idle,160)
    }
    window.addEventListener('scroll',scroll,{passive:true})
    return()=>{window.removeEventListener('scroll',scroll);clearTimeout(timer);scrolling.current=false;queue.scrollBusy(false)}
  },[queue])

  useEffect(() => {
    let active = true
    const unsubscribe = queue.subscribe((status) => { if (active) setQueueState(status) })
    const visibility = () => queue.pageHidden(document.hidden)
    document.addEventListener('visibilitychange', visibility); visibility()
    queue.pauseBackground(viewing.current || stopped.current)
    if (desktop) void desktop.request<{allPaused:boolean}>('status').then((status) => { if (active) { stopped.current = status.allPaused; setIndexingStopped(status.allPaused); queue.pauseBackground(viewing.current || stopped.current) } }).catch(() => {})
    void loadLibrary().then(({ items, folders }) => {
      if (!active) return
      records.current = new Map(items.map((item) => [item.id, item])); folderRecords.current = folders
      setItems(items); setFolders(folders); setLoading(false)
      void storageInfo().then((info) => { if (active) setStorage(info) }).catch(() => {})
    }).catch((error) => { if (active) { setNotice(`Local storage is unavailable: ${message(error)}. Your folders cannot be saved in this browser mode.`); setLoading(false) } })
    return () => { active = false; unsubscribe(); queue.pauseBackground(true); document.removeEventListener('visibilitychange', visibility) }
  }, [queue])

  useEffect(() => {
    if (!desktop) return
    let active=true, previous='';let timer:ReturnType<typeof setTimeout>
    const poll=async()=>{
      try {
        if(!scanBusy.current) {
          const progress=await desktop!.request<{state:string;done:number;current:string;error?:string}>('scanStatus')
          const signature=`${progress.state}:${progress.done}`
          if(active && signature!==previous && (['scanning','paused'].includes(progress.state)||previous.startsWith('scanning')||previous.startsWith('paused'))) {
            const saved=await loadLibrary()
            if(active) { records.current=new Map(saved.items.map((item)=>[item.id,item]));folderRecords.current=saved.folders;setItems(saved.items);setFolders(saved.folders);setScan(['scanning','paused'].includes(progress.state)?`Disk scan ${progress.state} · ${progress.done.toLocaleString()} media found`:'');if(progress.error)setNotice(progress.error) }
          }
          previous=signature
        }
      } catch(error){if(active)setNotice(message(error))}
      if(active)timer=setTimeout(()=>void poll(),2500)
    }
    void poll();return()=>{active=false;clearTimeout(timer)}
  },[])

  useEffect(() => {
    if (!desktop) return
    let active=true,since=0,timer:ReturnType<typeof setTimeout>
    const poll=async()=>{
      try {
        const result=await desktop!.request<{revision:number;done:number;total:number;dated:number;state:string;updates:Array<{media_id:string;captured_at:string|null;source:string}>}>('captureDates',{since})
        if(active){setCaptureProgress({done:result.done,total:result.total,dated:result.dated,state:result.state});since=result.revision;for(const update of result.updates){const item=records.current.get(update.media_id);if(item&&(item.capturedAt??null)!==update.captured_at)mergePatch(update.media_id,{capturedAt:update.captured_at,captureDate:update.captured_at?.slice(0,10)??null,dateSource:update.source,date:update.captured_at?.slice(0,10)??'Unknown capture date'},item.version)}}
      } catch { /* Existing media stays browsable while the metadata worker is unavailable. */ }
      if(active)timer=setTimeout(()=>void poll(),5000)
    }
    void poll();return()=>{active=false;clearTimeout(timer)}
  },[mergePatch])

  const thumbnail = useCallback((media: RuntimeMedia, priority = 1): Promise<PreviewRecord> => {
    const key = `${media.id}@${media.version}:thumbnail`
    return queue.enqueue(key, media.name, priority, async (signal) => {
      if (records.current.get(media.id)?.version !== media.version) throw new Error('Preview superseded by a newer scan')
      mergePatch(media.id, { processing: 'processing' }, media.version)
      try {
        const preview = await makeThumbnail(media, signal)
        if (records.current.get(media.id)?.version === media.version) mergePatch(media.id, { processing: 'ready', thumbnailReady: true, duration: preview.duration, error: undefined }, media.version)
        return preview
      } catch (error) {
        if (records.current.get(media.id)?.version === media.version) mergePatch(media.id, { processing: signal.aborted ? 'queued' : 'failed', thumbnailReady: false, error: signal.aborted ? undefined : message(error) }, media.version)
        throw error
      }
    })
  }, [mergePatch, queue])
  useEffect(() => {
    if(!desktop)return
    let active=true,since=0,timer:ReturnType<typeof setTimeout>
    const poll=async()=>{
      try{
        const result=await desktop!.request<{revision:number;done:number;total:number;state:string;updates:Array<{media_id:string;categories:string[];source:string}>}>('categories',{since})
        if(active){setCategoryProgress({done:result.done,total:result.total,state:result.state});since=result.revision;for(const row of result.updates){const item=records.current.get(row.media_id);if(item&&(JSON.stringify(item.categories)!==JSON.stringify(row.categories)||item.categorySource!==row.source))mergePatch(row.media_id,{categories:row.categories,categorySource:row.source},item.version)}}
      }catch{/* Saved categories remain available offline. */}
      if(active)timer=setTimeout(()=>void poll(),5000)
    }
    void poll();return()=>{active=false;clearTimeout(timer)}
  },[mergePatch])

  useEffect(() => {
    for (const media of items) {
      if (!['image', 'raw', 'video'].includes(media.kind)) continue
      if (media.thumbnailReady && media.processing === 'ready') continue
      const key = `${media.id}@${media.version}:thumbnail`
      if (scheduled.current.has(key)) continue
      scheduled.current.add(key)
      void thumbnail(media).catch(() => {})
    }
  }, [items, thumbnail])

  const visibleThumbnail = useCallback(async (media: RuntimeMedia, priority: number) => {
    // A cache hit needs no decoder and remains available while playback is paused.
    const cached = await getPreview(media, 'thumbnail')
    return cached ?? thumbnail(media, priority)
  }, [thumbnail])
  const prioritize = useCallback((media: RuntimeMedia, priority: number) => {
    queue.reprioritize(`${media.id}@${media.version}:thumbnail`, priority)
    if (!desktop) return
    if (priority > 1) facePriority.current.set(media.id, priority); else facePriority.current.delete(media.id)
    if (!facePriorityTimer.current) facePriorityTimer.current = setTimeout(() => {
      facePriorityTimer.current = undefined
      const priorityIds = [...facePriority.current].sort((a,b) => b[1]-a[1]).map(([id]) => id).slice(0,100)
      void desktop!.request('faceControl', { priority: priorityIds }).catch(() => {})
    }, 500)
  }, [queue])
  const pauseForViewer = useCallback((value: boolean) => { viewing.current = value; queue.pauseBackground(value || scanBusy.current || stopped.current); if (desktop) void desktop.request('faceControl', { paused: value || scrolling.current }).catch(() => {}) }, [queue])
  const stopIndexing = useCallback(async (value: boolean) => {
    stopped.current = value; setIndexingStopped(value); queue.pauseBackground(value || viewing.current || scanBusy.current)
    try { localStorage.setItem('vela-indexing-stopped', String(value)); if (desktop) await desktop.request('indexControl', { paused: value }) }
    catch (error) { setNotice(message(error)) }
  }, [queue])
  const open = useCallback((media: RuntimeMedia) => {
    // Native browser playback does not wait behind an image conversion.
    if (media.kind !== 'raw' && !['HEIC', 'HEIF','TIF','TIFF','PSD','JP2','J2K','JXL'].includes(media.extension)) return openOriginal(media)
    // Foreground conversion has its own native lane, not the thumbnail queue.
    return openOriginal(media)
  }, [])
  const prefetch = useCallback((media: RuntimeMedia, signal?: AbortSignal) => {
    if (media.byteSize > 64 * 1024 * 1024) return Promise.reject(new Error('Large neighbor opens on demand'))
    const key=`${media.id}@${media.version}:full`
    const cancel=()=>queue.cancelPending(key,1000)
    signal?.addEventListener('abort',cancel,{once:true})
    // Never abort an in-flight decoder: navigation may have promoted the same
    // shared promise to the foreground. Only stale pending work is cancelled.
    return queue.enqueue(key, `Loading adjacent ${media.name}`, 1000, queuedSignal => openOriginal(media,queuedSignal,true)).finally(()=>signal?.removeEventListener('abort',cancel))
  }, [queue])

  const importEntries = useCallback(async (folder: FolderRecord, entries: AsyncIterable<ImportEntry>) => {
    if (scanBusy.current) { setNotice('A folder scan is already running. Add the next folder after it finishes.'); return }
    scanBusy.current = true; queue.pauseBackground(true); setNotice(''); setScan(`Scanning ${folder.name}…`)
    const present = new Set<string>(); let batch: RuntimeMedia[] = []; let sources: SourceRecord[] = []; let count = 0
    const commit = async () => {
      if (!batch.length) return
      await saveImportBatch(batch, sources)
      const persisted = batch; batch = []; sources = []
      for (const media of persisted) {
        const key = `${media.id}@${media.version}:thumbnail`
        const old = records.current.get(media.id)
        if (old?.version !== media.version || old.processing === 'failed') scheduled.current.delete(key)
        records.current.set(media.id, media)
      }
      setItems(Array.from(records.current.values()))
      setScan(`Saving ${folder.name} · ${count.toLocaleString()} files`)
      await new Promise<void>((resolve) => setTimeout(resolve, 0))
    }
    try {
      await saveFolder(folder)
      folderRecords.current = [...folderRecords.current.filter((entry) => entry.id !== folder.id), folder]
      setFolders(folderRecords.current)
      setScan(`Scanning ${folder.name}…`)
      for await (const entry of entries) {
        const media = createRuntimeMedia(entry.file, count, folder.id, entry.path)
        const old = records.current.get(media.id)
        media.favorite = old?.favorite ?? false
        if (old?.version === media.version && old.processing !== 'failed') { media.thumbnailReady = old.thumbnailReady; media.processing = old.processing; media.duration = old.duration }
        present.add(media.id); batch.push(media); sources.push({ id: media.id, handle: entry.handle, file: entry.handle ? undefined : entry.file }); count++
        if (batch.length >= 40) await commit()
      }
      await commit()
      await removeMissing(folder.id, present)
      for (const [id, media] of records.current) if (media.folderId === folder.id && !present.has(id)) records.current.delete(id)
      setItems(Array.from(records.current.values()))
      const saved = { ...folder, count }
      await saveFolder(saved); folderRecords.current = folderRecords.current.map((entry) => entry.id === folder.id ? saved : entry); setFolders(folderRecords.current)
      const persistent = await requestPersistentStorage(); setStorage({ ...await storageInfo(), persistent })
    } catch (error) {
      setNotice(`Could not finish saving ${folder.name}: ${message(error)}. Files saved before this error remain in your library. If browser storage is full, use linked folder access in Chrome or Edge.`)
    } finally { setScan(''); scanBusy.current = false; queue.pauseBackground(viewing.current || stopped.current) }
  }, [queue])

  const addHandle = useCallback(async (handle: LocalDirectoryHandle, existingId?: string) => {
    let existing = folderRecords.current.find((folder) => folder.id === existingId)
    if (!existing) for (const folder of folderRecords.current) if (folder.handle && await folder.handle.isSameEntry(handle)) { existing = folder; break }
    const folder: FolderRecord = { id: existing?.id ?? crypto.randomUUID(), name: handle.name, mode: 'linked', addedAt: existing?.addedAt ?? Date.now(), handle, count: existing?.count ?? 0 }
    await importEntries(folder, walkDirectory(handle))
  }, [importEntries])

  const addFiles = useCallback(async (files: File[], existingId?: string) => {
    if (!files.length) return
    const name = files[0].webkitRelativePath.split('/')[0] || 'Selected folder'
    let existing = folderRecords.current.find((folder) => folder.id === existingId)
    if (!existing) existing = folderRecords.current.find((folder) => ['copied', 'session'].includes(folder.mode) && folder.name === name && files.slice(0, 10).some((file) => {
      const media = records.current.get(`${folder.id}/${file.webkitRelativePath.split('/').slice(1).join('/') || file.name}`)
      return media?.byteSize === file.size && media.modified === file.lastModified
    }))
    const folder: FolderRecord = { id: existing?.id ?? crypto.randomUUID(), name, mode: 'session', addedAt: existing?.addedAt ?? Date.now(), count: existing?.count ?? 0 }
    await importEntries(folder, (async function* () { for (const file of files) yield { file, path: file.webkitRelativePath.split('/').slice(1).join('/') || file.name } })())
  }, [importEntries])

  const reconnect = useCallback(async (folder: FolderRecord) => {
    try {
      if (!folder.handle) return false
      const permission = await folder.handle.queryPermission({ mode: 'read' })
      if (permission !== 'granted' && await folder.handle.requestPermission({ mode: 'read' }) !== 'granted') { setNotice('Folder access was not granted. Cached previews remain available.'); return true }
      await addHandle(folder.handle, folder.id); return true
    } catch (error) { setNotice(`Folder could not be opened: ${message(error)}. Select it again using Add folder.`); return true }
  }, [addHandle])

  const favorite = useCallback(async (media: RuntimeMedia) => {
    const value = !media.favorite
    try { await saveFavorite(media.id, value); mergePatch(media.id, { favorite: value }) }
    catch (error) { setNotice(`Could not save favorite: ${message(error)}`) }
  }, [mergePatch])
  const selectionAction = useCallback(async (ids: string[], action: 'deleteMedia' | 'shareMedia') => {
    if (!desktop) { setNotice('Deleting originals and exporting selections are available in the desktop app. Browser folder access is read-only.'); return }
    try {
      const result=await desktop.request<{removed?:string[];copied?:string[];failures:string[]} | null>(action,{ids})
      if(!result)return false
      const saved=await loadLibrary(); records.current=new Map(saved.items.map(item=>[item.id,item])); setItems(saved.items);setFolders(saved.folders)
      setNotice(`${action==='deleteMedia'?'Moved to Recycle Bin':'Copied for sharing'}: ${(result.removed??result.copied??[]).length} files.${result.failures.length?' '+result.failures.join(' · '):''}`)
      return true
    }catch(error){setNotice(message(error));return false}
  }, [])
  const assignCategory = useCallback(async (ids:string[],category:string) => {
    try{await desktop!.request('assignCategories',{ids,categories:[category]});const saved=await loadLibrary();records.current=new Map(saved.items.map(item=>[item.id,item]));setItems(saved.items);setNotice(`Category saved for ${ids.length} items. Your correction takes priority over automatic suggestions.`);return true}catch(error){setNotice(message(error));return false}
  },[])
  const nativeFolder = useCallback(async (folder?: FolderRecord, relink = false, sourcePath?: string) => {
    if (!desktop || scanBusy.current) return
    scanBusy.current = true; queue.pauseBackground(true); setNotice(''); setScan(folder ? `Updating ${folder.name}…` : 'Scanning selected folder…')
    try {
      const result = await desktop.request<{ warnings?: string[]; backgroundScan?:boolean } | null>(sourcePath ? 'addDiskFolder' : folder ? relink ? 'relinkFolder' : 'rescanFolder' : 'addFolder', { folderId: folder?.id, path:sourcePath })
      if (!result) return
      if(result.backgroundScan) {
        queue.pauseBackground(viewing.current || stopped.current)
        while(true) {
          const progress=await desktop.request<{state:string;done:number;current:string;error?:string}>('scanStatus')
          setScan(progress.state==='paused' ? 'Media scan paused · Resume all indexing to continue' : `Scanning media · ${progress.done.toLocaleString()} files found · ${progress.current}`)
          const partial=await loadLibrary();records.current=new Map(partial.items.map((item)=>[item.id,item]));folderRecords.current=partial.folders;setItems(partial.items);setFolders(partial.folders)
          if(!['scanning','paused'].includes(progress.state)) { if(progress.error)setNotice(progress.error); break }
          await new Promise((resolve)=>setTimeout(resolve,1500))
        }
      }
      const saved = await loadLibrary(); records.current = new Map(saved.items.map((item) => [item.id, item])); folderRecords.current = saved.folders
      scheduled.current.clear(); setItems(saved.items); setFolders(saved.folders); setStorage(await storageInfo())
      if (result.warnings?.length) setNotice(`Some files could not be read; their previous catalog entries were kept. ${result.warnings.join(' · ')}`)
    } catch (error) { setNotice(message(error)) }
    finally { scanBusy.current = false; setScan(''); queue.pauseBackground(viewing.current || stopped.current) }
  }, [queue])
  return { items, folders, loading, notice, setNotice, scan, captureProgress, categoryProgress, queueState, storage, addHandle, addFiles, reconnect, favorite, selectionAction, assignCategory, open, prefetch, pauseForViewer, visibleThumbnail, prioritize, nativeFolder, indexingStopped, stopIndexing }
}

async function* walkDirectory(handle: LocalDirectoryHandle, prefix = ''): AsyncGenerator<ImportEntry> {
  for await (const entry of handle.values()) {
    const path = `${prefix}${entry.name}`
    if (entry.kind === 'directory') yield* walkDirectory(entry, `${path}/`)
    else yield { file: await entry.getFile(), path, handle: entry }
  }
}
function message(error: unknown) { return error instanceof Error ? error.message : error && typeof error === 'object' && 'message' in error ? String(error.message) : String(error) }
