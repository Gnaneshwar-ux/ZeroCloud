import { desktop, type EngineStatus } from '../lib/desktop'
import { Icon } from './Icon'
import { useState } from 'react'
import './IndexJobCards.css'
export function EngineActivity({ status, onPeople, onPlaces, stopped, onStop }: { status?: EngineStatus; onPeople: () => void; onPlaces: () => void; stopped: boolean; onStop: (value: boolean) => void }) {
  const [rescanning,setRescanning]=useState<string|null>(null),[messages,setMessages]=useState<Record<string,string>>({})
  const rescan=async(kind:'faces'|'locations')=>{
    if(!window.confirm(kind==='locations'?'Re-read GPS metadata from all linked photos? Existing map locations stay available until refreshed. No originals or face groups are changed.':'Recheck faces in linked photos? Your names and saved corrections are retained. Originals are not changed.'))return
    setRescanning(kind)
    try{await desktop!.request(kind==='locations'?'refindLocations':'refindFaces');setMessages(old=>({...old,[kind]:stopped?'Rescan queued · resume all indexing to start.':'Rescan queued · saved data stays available while refreshed.'}))}
    catch(error){setMessages(old=>({...old,[kind]:String(error)}))}finally{setRescanning(null)}
  }
  return <section className="indexing-controls" aria-label="Background indexing">
    <div className="indexing-summary"><strong>{stopped ? 'All indexing stopped' : 'Background indexing'}</strong><span>{stopped ? 'Saved progress is kept. An in-flight item may finish; no new background jobs start.' : 'Thumbnails, people and GPS metadata · Open files get priority.'}</span><button className="primary-action" onClick={() => onStop(!stopped)}>{stopped ? 'Resume all indexing' : 'Stop all indexing'}</button></div>
    {desktop && status && <>
      <div className="index-job-grid">
        <article className="index-job-card" aria-label="Face indexing"><div className="index-job-heading"><Icon name="people" size={17}/><span><strong>{stopped ? 'Faces stopped' : status.error ? 'Faces need attention' : status.paused ? 'Faces paused for viewing' : !status.total ? 'No photos to index' : status.enabled ? status.state==='loading' ? 'Loading face model…' : status.done>=status.total ? 'Face index up to date' : 'Finding faces' : 'Faces paused'}</strong><small>{status.done.toLocaleString()} / {status.total.toLocaleString()} checked · {status.failed} errors</small></span></div><div className="button-row"><button onClick={onPeople}>View people</button><button disabled={!!rescanning} onClick={()=>void rescan('faces')}>{rescanning==='faces'?'Queuing…':'Rescan faces'}</button></div>{messages.faces&&<small role="status">{messages.faces}</small>}</article>
        <article className="index-job-card" aria-label="Location indexing"><div className="index-job-heading"><Icon name="places" size={17}/><span><strong>{stopped ? 'Locations stopped' : status.paused ? 'Locations paused for viewing' : status.locations?.state==='complete' ? 'GPS index up to date' : 'Reading photo locations'}</strong><small>{status.locations?.done.toLocaleString() ?? '0'} / {status.locations?.total.toLocaleString() ?? status.total.toLocaleString()} checked · {status.locations?.tagged.toLocaleString() ?? '0'} GPS photos · {status.locations?.failed ?? 0} errors</small></span></div><div className="button-row"><button onClick={onPlaces}>View map</button><button disabled={!!rescanning} onClick={()=>void rescan('locations')}>{rescanning==='locations'?'Queuing…':'Rescan locations'}</button></div>{messages.locations&&<small role="status">{messages.locations}</small>}</article>
      </div>
      {status.scan && ['scanning','paused'].includes(status.scan.state) && <div className="scan-progress" role="status">Disk scan {status.scan.state} · {status.scan.done.toLocaleString()} media found{status.scan.current && ` · ${status.scan.current}`}</div>}
      {status.cacheError && <div className="scan-progress" role="alert">{status.cacheError} · Reconnect the cache drive and sync again before removing it.</div>}
      {status.error && <div className="scan-progress" role="alert">{status.error}</div>}
    </>}
  </section>
}
