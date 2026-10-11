import { useCallback, useEffect, useRef, useState } from 'react'
import { desktop } from '../lib/desktop'
import { Viewer } from './Viewer'
import { Icon } from './Icon'
import type { RuntimeMedia } from '../lib/runtimeMedia'
import './Memories.css'
import './MemoryEditor.css'
type Memory = {id:string;title:string;category:string;status:string;created:number;error:string;style?:string;soundtrack?:string;credits?:string;parent_id?:string}
type Overview = {items:Memory[];enabled:boolean;paused:boolean;onlineMusic:boolean;state:string;current:string;cacheBytes:number;budgetBytes:number;notify:boolean;requestedId?:string;musicState?:string;cachedMusic?:string[]}
type Subject = {id:string;title:string;category:string;count:number}
const openMemory = (media:RuntimeMedia) => Promise.resolve(`vela://memory/${encodeURIComponent(media.id)}`)
const noAction = () => {}
export function MemoryNotice({onOpen}:{onOpen:()=>void}) {
 const [show,setShow]=useState(false)
 useEffect(()=>{
  if (!desktop) return
  const poll=()=>void desktop!.request<Overview>('memory:notice').then(r=>{if(r.notify)setShow(true)}).catch(()=>{})
  poll(); const timer=setInterval(poll,30000);return()=>clearInterval(timer)
 },[])
 if(!show)return null
 return <div className="memory-notice" role="status"><span>A new memory is ready</span><button onClick={()=>{setShow(false);onOpen();void desktop?.request('memory:ack')}}>View memory</button><button aria-label="Dismiss memory notice" onClick={()=>{setShow(false);void desktop?.request('memory:ack')}}>×</button></div>
}
export function Memories({onPlaying}:{onPlaying:(value:boolean)=>void}) {
 const [data,setData]=useState<Overview|null>(null),[error,setError]=useState(''),[filter,setFilter]=useState('all'),[playing,setPlaying]=useState<Memory|null>(null)
 const [music,setMusic]=useState('auto')
 const [subjects,setSubjects]=useState<Subject[]>([]),[subjectType,setSubjectType]=useState('places'),[selection,setSelection]=useState(''),[loadingSubjects,setLoadingSubjects]=useState(false)
 const [style,setStyle]=useState('auto'),[requesting,setRequesting]=useState(false),[waitingFor,setWaitingFor]=useState('')
 const waitingForRef=useRef('')
 const preferenceRevision=useRef(0),preferenceBusy=useRef(false),mounted=useRef(true)
 const [savingPreference,setSavingPreference]=useState(false)
 const closeMemory=useCallback(()=>setPlaying(null),[])
 const refresh=()=>{
  if(preferenceBusy.current)return
  const revision=preferenceRevision.current
  return desktop?.request<Overview>('memory:overview').then(next=>{
   if(!mounted.current||revision!==preferenceRevision.current||preferenceBusy.current)return
   setData(next);const item=next.items.find(value=>value.id===waitingForRef.current);if(item?.status==='ready'){setPlaying(item);waitingForRef.current='';setWaitingFor('')}else if(item?.status==='failed'){setError(item.error||'This reel needs attention.');waitingForRef.current='';setWaitingFor('')}
  }).catch(e=>{if(mounted.current&&revision===preferenceRevision.current)setError(String(e))})
 }
 const setPreference=async(patch:Partial<Pick<Overview,'enabled'|'paused'|'onlineMusic'>>)=>{
  if(!data||preferenceBusy.current)return
  const previous={enabled:data.enabled,paused:data.paused,onlineMusic:data.onlineMusic}
  preferenceBusy.current=true;preferenceRevision.current++;setSavingPreference(true);setError('')
  setData(current=>current?{...current,...patch}:current)
  try{
   const next=await desktop!.request<Overview>('memory:control',patch)
   if(mounted.current)setData(next)
  }catch(reason){
   if(mounted.current){setData(current=>current?{...current,...previous}:current);setError(`Could not save the preference. Your previous setting was restored. ${String(reason)}`)}
  }finally{
   preferenceRevision.current++;preferenceBusy.current=false
   if(mounted.current)setSavingPreference(false)
  }
 }
 useEffect(()=>{onPlaying(!!playing);return()=>onPlaying(false)},[playing,onPlaying])
 useEffect(()=>{mounted.current=true;void refresh();void desktop?.request('memory:ack');const t=setInterval(()=>void refresh(),3000);return()=>{mounted.current=false;clearInterval(t)}},[])
 const action=async(op:string,args:Record<string,unknown>={})=>{const revision=preferenceRevision.current;try{setError('');const next=await desktop!.request<Overview>(`memory:${op}`,args);if(!mounted.current)return;if(revision===preferenceRevision.current&&!preferenceBusy.current)setData(next);if(next.requestedId){waitingForRef.current=next.requestedId;setWaitingFor(next.requestedId)}}catch(e){if(mounted.current)setError(String(e))}}
 const loadSubjects=useCallback(async()=>{setLoadingSubjects(true);try{const result=await desktop!.request<{options:Subject[]}>('memory:options');if(mounted.current)setSubjects(result.options)}catch(e){if(mounted.current)setError(String(e))}finally{if(mounted.current)setLoadingSubjects(false)}},[])
 useEffect(()=>{if(desktop)void loadSubjects()},[loadSubjects])
 const surprise=async(id?:string)=>{setRequesting(true);try{await action(id?'variation':'surprise',{id,style,music,category:'all'})}finally{setRequesting(false)}}
 const create=async()=>{setRequesting(true);try{await action('create',{selection,category:subjectType,style,music,force:true})}finally{setRequesting(false)}}
 const ready=data?.items.filter(m=>m.status==='ready'&&(filter==='all'||m.category.split(',').includes(filter)))??[]
 const pending=data?.items.filter(m=>['queued','rendering','failed'].includes(m.status))??[]
 if(!desktop)return <section className="memories-workspace"><h1>Memories</h1><p>Local reel generation is available in the desktop app. Your photos stay on your device.</p></section>
 return <section className="memories-workspace"><div className="section-heading"><div><h1>Memories</h1><p>Your people, places and little adventures — brought to life.</p></div><button disabled={loadingSubjects} onClick={()=>void loadSubjects()}>{loadingSubjects?'Loading choices…':'Refresh choices'}</button></div>
  <div className="memory-controls"><label><input type="checkbox" disabled={!data||savingPreference} checked={data?.enabled??false} onChange={e=>void setPreference({enabled:e.target.checked})}/> Create automatically when idle</label><button disabled={!data||savingPreference} onClick={()=>void setPreference({paused:!data?.paused})}>{data?.paused?'Resume generation':'Pause generation'}</button><span>{savingPreference?'Saving preference…':data?.paused?'Paused':data?.state??'Loading…'}{data?.current&&` · ${data.current}`}</span></div>
  <p className="memory-help">Animated highlights, music and moments — saved on this device. Reels: {((data?.cacheBytes??0)/1048576).toFixed(1)} / 512 MB.</p>
  <div className="memory-creative-tools"><div><strong>Create a reel</strong><p>Choose a subject, or let Surprise me pick a person, place or trip and its mix of moments.</p></div><label>Story about<select aria-label="Story about" value={subjectType} onChange={event=>{setSubjectType(event.target.value);setSelection('')}}><option value="places">A place</option><option value="people">A person</option><option value="trips">A trip</option></select></label><label>Choose subject<select aria-label="Choose subject" value={selection} disabled={loadingSubjects} onChange={event=>setSelection(event.target.value)}><option value="">Choose {subjectType==='people'?'a person':subjectType==='places'?'a place':'a trip'}…</option>{subjects.filter(s=>s.category===subjectType).map((s,index)=><option key={s.id} value={s.id}>{s.title==='Together'?`Person ${index+1}`:s.title} · {s.count} items</option>)}</select></label><label>Reel style<select aria-label="Reel style" value={style} onChange={event=>setStyle(event.target.value)}><option value="auto">Surprise style</option><option value="daydream">Daydream · gentle and melodic</option><option value="postcards">Postcards · travel and movement</option><option value="spotlight">Spotlight · upbeat moments</option><option value="joyful">Good times · playful and bright</option><option value="goldenhour">Golden hour · warm and unhurried</option><option value="cinema">Scenic cinema · spacious and calm</option></select></label><label>Music mood<select aria-label="Music mood" value={music} onChange={event=>setMusic(event.target.value)}><option value="auto">Match the story</option><option value="joyful">Joyful · upbeat and bright</option><option value="relaxed">Relaxed · warm and acoustic</option><option value="cinematic">Cinematic · calm and spacious</option></select></label><div className="memory-builder-actions"><button className="primary-action" disabled={requesting||!selection} onClick={()=>void create()}>{requesting?'Adding reel…':'Generate reel'}</button><button className="secondary-action" disabled={requesting} onClick={()=>void surprise()}>{requesting?'Finding a new story…':'Surprise me'}</button></div></div>
  {!!waitingFor&&<div className="memory-awaiting" role="status"><span>{data?.paused?'Generation is paused. Resume above to finish your reel.':`${data?.current||'Your new reel is queued'} · opens when ready`}</span><button onClick={()=>{waitingForRef.current='';setWaitingFor('')}}>I’ll watch later</button></div>}
  <details className="memory-soundtrack-settings"><summary>Music & online options</summary><p>Six original instrumental arrangements are available offline: marimba, coastal plucks, pop, celebratory bells, warm acoustic and soft cinematic pads. Each reel gets its own musical variation. The optional music pack adds recorded tracks including Carefree, Life of Riley and Daily Beetle, downloaded from Incompetech and cached here. Only the music provider is contacted; photos, videos, faces and locations never leave your device.</p><label className="memory-preference-toggle"><input type="checkbox" disabled={!data||savingPreference} checked={!!data?.onlineMusic} onChange={event=>void setPreference({onlineMusic:event.target.checked})}/>Allow optional online music</label><div className="button-row"><button disabled={savingPreference||!data?.onlineMusic||data?.musicState?.startsWith('Downloading')} onClick={()=>void action('downloadMusic')}>Download music pack</button><small>{!data?.onlineMusic?'Online music off · original soundtracks remain available':data?.musicState||'Original soundtracks · offline ready'} · {data?.cachedMusic?.length||0} tracks cached</small></div><p>Downloads are optional. When offline or a download fails, reels use the original score. Downloaded music includes credits in the reel and its saved details.</p></details>
  <details className="memory-help"><summary>How memories are made</summary><p>Automatic creation waits for quiet time; stopped indexing counts as idle. Generate now bypasses load checks, one reel at a time, and may use more CPU. Pause generation still works.</p><p>Portrait reels, or widescreen Scenic cinema, with varied pans, zooms and transitions, plus six original instrumental arrangements. Photos are kept in frame rather than aggressively cropped. At most one automatic reel and one quiet notice per day. Generation pauses while browsing or viewing, or when CPU load rises.</p><p>Trips are suggestions based on folders and camera-date gaps; locations use GPS clusters and offline nearby town names. Selection balances people and scene categories, favours sharper, well-exposed previews and reduces near-identical shots. Scenery is included when suitable matching photos are available; scene labels can be imperfect. Video excerpts use visual sharpness, not semantic understanding. Nothing is uploaded; original files stay untouched.</p></details>
  {error&&<p role="alert">{error}</p>}
  {!!pending.length&&<details className="memory-jobs" open><summary>Reel activity · {pending.length}</summary>{pending.map(m=><div key={m.id} className="memory-job"><span><strong>{m.title}</strong><small>{m.status==='failed'?m.error||'Could not finish this reel':m.status==='rendering'?'Creating reel…':data?.paused?'Queued · generation paused':'Queued · one reel at a time'}</small></span>{m.status==='failed'&&<button onClick={()=>void action('queue',{id:m.id,force:true})}>Retry</button>}</div>)}</details>}
  <div className="memory-filters" aria-label="Memory categories">{['all','people','places','trips'].map(f=><button key={f} aria-pressed={filter===f} onClick={()=>setFilter(f)}>{f==='all'?'Saved reels':f[0].toUpperCase()+f.slice(1)}</button>)}</div>
  <div className="memory-grid">{ready.map(m=><article key={m.id} className="memory-card"><div className="memory-cover"><span>✦</span><small>{m.category.split(',').join(' · ')}</small></div><h2>{m.title}</h2><small className="memory-style">{m.style||'Classic'} · {m.soundtrack||'Original soundtrack'}</small><p>Saved reel · ready to play</p><div className="button-row"><button onClick={()=>setPlaying(m)}>Play memory</button><button className="another-memory" disabled={requesting} onClick={()=>void surprise(m.id)}>Make another version</button></div>{m.credits&&<details className="memory-credits"><summary>Music credits</summary><p>{m.credits}</p></details>}</article>)}</div>
  {data&&!ready.length&&<div className="empty-state"><div className="memory-empty-icon"><Icon name="video" size={26}/></div><strong>No finished reels here yet</strong><p>Choose a subject above and Generate reel, or try Surprise me. Only completed reels appear in this collection.</p></div>}
  {playing&&<Viewer key={playing.id} media={{id:playing.id,name:playing.title,kind:'video',extension:'MP4',date:'Generated memory',size:'Saved local reel',accent:'',folderId:'memories',relativePath:`memories/${playing.id}.mp4`,byteSize:0,modified:playing.created,version:String(playing.created),processing:'ready'}} open={openMemory} onClose={closeMemory} onFavorite={noAction} allowFavorite={false} onNavigate={noAction} hasPrevious={false} hasNext={false} position="1 / 1"/>}
 </section>
}
