import { useEffect, useState } from 'react'
import { desktop, type StorageStatus } from '../lib/desktop'
import { formatBytes } from '../lib/runtimeMedia'
import { Icon } from './Icon'

export function StorageSettings({ onError, onStop }: { onError: (message: string) => void; onStop:(value:boolean)=>Promise<void> }) {
  const [storage, setStorage] = useState<StorageStatus>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => { if (desktop) void desktop.request<StorageStatus>('storage').then(setStorage).catch((error) => onError(String(error))) }, [onError])
  async function choose(op: 'chooseStorage' | 'openStorage' | 'moveStorage') {
    setBusy(true); onError('')
    try { if (await desktop!.request(op)) window.location.reload() }
    catch (error) { onError(String(error)) }
    finally { setBusy(false) }
  }
  async function prepare() {
    setBusy(true)
    try { await onStop(true); await desktop!.request('backup'); setMessage('Indexing is paused. Close ZeroCloud before copying the entire data folder for a consistent backup. Your originals must be backed up separately.') }
    catch (error) { onError(String(error)) }
    finally { setBusy(false) }
  }
  async function workspace(op:'saveWorkspace'|'openWorkspace') {
    setBusy(true);onError('');setMessage('')
    try {
      const result=await desktop!.request<{path:string;folders:number}|null>(op)
      if(result){if(op==='openWorkspace')window.location.reload();else setMessage(`Workspace saved: ${result.path}. ${result.folders} folder paths and cache locations are linked. Keep the actual catalog and cache folders with this file.`)}
    }catch(error){onError(String(error))}finally{setBusy(false)}
  }
  async function cacheLimit(value: string) {
    try { setStorage(await desktop!.request('cacheLimit', { megabytes: Number(value) })) }
    catch (error) { onError(String(error)) }
  }
  return <section className="settings-section"><div className="section-heading"><div><h2>Library storage</h2><p>A small, portable catalog. Your originals remain in their folders.</p></div></div>
    <article className="settings-card"><div className="settings-card-heading"><Icon name="storage" /><h3>Data & cache folder</h3></div>{desktop ? <><label className="path-label">Current library folder</label><output className="storage-path">{storage?.path || 'Loading…'}</output><div className="storage-metrics"><span><strong>{storage ? formatBytes(storage.cacheBytes) : '—'}</strong>Small previews & faces</span><span><strong>{storage ? formatBytes(storage.databaseBytes) : '—'}</strong>Catalog & face vectors</span><span><strong>0</strong>Original file copies</span></div><div className="button-row"><button className="primary-action" disabled={busy} onClick={() => void choose('chooseStorage')}>Choose new data folder</button><button disabled={busy} onClick={() => void choose('openStorage')}>Open existing library</button><button disabled={busy} onClick={() => void prepare()}>Prepare for backup</button></div><p>Choosing a new folder starts a separate library; your previous library is kept. To reuse or import saved data, select <strong>Open existing library</strong>. No automatic copying of originals or of the previous library.</p></> : <p>The standalone browser page keeps its catalog and small thumbnails in browser storage. For a selectable data folder and importing that folder on another computer, use <strong>Launch ZeroCloud.cmd</strong>. Browser folder uploads now keep originals in memory only; reconnect them after refresh. Existing browser data has not been deleted.</p>}</article>
    {desktop && <article className="settings-card"><div className="settings-card-heading"><Icon name="folders"/><h3>Saved workspaces</h3></div><p>Save a small workspace file linking this catalog, original folders, portable cache paths and available backup state. Open it to load the saved library and its cached previews immediately, without a new folder scan.</p><div className="button-row"><button className="primary-action" disabled={busy} onClick={()=>void workspace('saveWorkspace')}>Save workspace</button><button disabled={busy} onClick={()=>void workspace('openWorkspace')}>Open workspace</button></div><p>The file links to your library; it is not a backup or a snapshot of old names and favorites. Keep the catalog and caches too. Relative paths support moving the same layout to another disk; offline originals stay listed and can be relinked.</p></article>}
    {desktop && <article className="settings-card"><h3>Thumbnail cache budget</h3><p>Keep small previews for quick returns. Least recently used thumbnails are removed when the budget is reached; names, face vectors, favorites, and originals are never removed.</p><select aria-label="Thumbnail cache budget" value={storage ? storage.cacheLimit / 1024 / 1024 : 512} onChange={(event) => void cacheLimit(event.target.value)}>{[128,256,512,1024].map((limit) => <option key={limit} value={limit}>{limit} MB</option>)}</select></article>}
    {desktop && <article className="settings-card"><h3>Change the storage location without losing data</h3><p>Copy your current catalog, small previews, people, and model files into an empty data folder, then use that folder. The previous data folder is retained as a backup. Original photos and videos are not copied.</p><button className="primary-action" disabled={busy} onClick={() => void choose('moveStorage')}>Move library data folder</button></article>}
    <article className="settings-card"><h3>Move your library to another computer</h3><ol><li>Use “Prepare for backup”, then close ZeroCloud.</li><li>Copy your selected ZeroCloud data folder, including <strong>vela-library.json</strong>, <strong>catalog.sqlite</strong>, thumbnails, face previews, and model files.</li><li>Install ZeroCloud on the other computer, then open that existing data folder.</li><li>Keep the same relative folder layout, or use <strong>Relink folder</strong> in Folders to locate your originals.</li></ol><p>Names, favorites, face vectors, and small thumbnails travel with the catalog. Photos and videos are never duplicated into it. Full-resolution conversions are memory-only; saved thumbnails stay within your budget (default 512 MB). Face previews and catalog data are separate from that thumbnail cap.</p></article>
    {message && <div className="settings-card" role="status">{message}</div>}
  </section>
}
