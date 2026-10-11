import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { desktop, faceUrl, type EngineStatus, type Person } from '../lib/desktop'
import { Icon } from './Icon'
import './PeopleActions.css'
const initial: EngineStatus = { state:'idle',current:'',done:0,total:0,failed:0,enabled:false,paused:false }
type Match={id:string;distance:number;faceIds:string[];previousNo:boolean}
type Appearance={id:string;mediaId:string;name:string}
type Organization={state:string;merged:number;checked:number;total:number;error:string;paused?:boolean}
type ReviewPair={first:string;second:string;distance:number;firstFaces:string[];secondFaces:string[]}
const unavailableFace='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160"><rect width="160" height="160" rx="16" fill="#27384a"/><circle cx="80" cy="60" r="22" fill="#8598ab"/><path d="M35 140v-14a45 45 0 0 1 90 0v14" fill="#8598ab"/></svg>')
function FaceThumbnail({id,alt='',loading='lazy'}:{id:string;alt?:string;loading?:'lazy'|'eager'}){
 const [preview,setPreview]=useState({id,attempt:0,failed:false}),timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined)
 const attempt=preview.id===id?preview.attempt:0,failed=preview.id===id&&preview.failed
 useEffect(()=>()=>clearTimeout(timer.current),[id])
 return <img src={failed?unavailableFace:faceUrl(id)+(attempt?`?retry=${attempt}`:'')} alt={alt} loading={loading} title={failed?'Face preview unavailable. Reopen this group to retry.':undefined} onError={()=>{if(attempt<2)timer.current=setTimeout(()=>setPreview({id,attempt:attempt+1,failed:false}),400*(attempt+1));else setPreview({id,attempt,failed:true})}}/>
}
export function People({onFilter,onError,active=true,exploring=false,personId,onReset,onGroups}:{onFilter:(person:Person)=>void;onError:(message:string)=>void;active?:boolean;exploring?:boolean;personId?:string;onReset?:()=>void;onGroups?:(people:Person[])=>void}) {
 const [people,setPeople]=useState<Person[]>([]),[status,setStatus]=useState(initial),[loading,setLoading]=useState(true)
 const [editing,setEditing]=useState<Person|null>(null),[name,setName]=useState(''),[selected,setSelected]=useState<string[]>([])
 const [dialog,setDialog]=useState<'same'|'reject'|'refind'|null>(null),[target,setTarget]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[search,setSearch]=useState('')
 const [review,setReview]=useState<Person|null>(null),[matches,setMatches]=useState<Match[]>([]),[anchorFaces,setAnchorFaces]=useState<string[]>([]),[reviewed,setReviewed]=useState(0)
 const [reviewTotal,setReviewTotal]=useState(0),[queueReview,setQueueReview]=useState(false)
 const skippedReviews=useRef(new Set<string>())
 const reviewBuffer=useRef<ReviewPair[]>([])
 const reviewSession=useRef(0)
 const [organization,setOrganization]=useState<Organization>({state:'idle',merged:0,checked:0,total:0,error:''})
 const [manage,setManage]=useState<Person|null>(null),[appearances,setAppearances]=useState<Appearance[]>([]),[faceTotal,setFaceTotal]=useState(0),[faceOffset,setFaceOffset]=useState(0),[faceSelection,setFaceSelection]=useState<string[]>([]),[moveTarget,setMoveTarget]=useState(''),[newName,setNewName]=useState(''),[correction,setCorrection]=useState<'move'|'new'|'reject'|null>(null)
 const [shown,setShown]=useState(60)
 const filteredPeople=[...people].sort((a,b)=>Number(b.isMe)-Number(a.isMe)||Number(!!b.name)-Number(!!a.name)||b.count-a.count).filter(p=>!search.trim()||`${p.name||'Unnamed person'} ${p.isMe?'me':''}`.toLowerCase().includes(search.trim().toLowerCase()))
 const visiblePeople=filteredPeople.slice(0,shown)
 const openPending=useCallback(async(nextPeople:Person[])=>{
  const session=reviewSession.current
  setBusy(true);setQueueReview(true)
  try{
   const pending=await desktop!.request<{total:number;pairs:ReviewPair[];pending:number}>('peopleReviews',{skip:[...skippedReviews.current]});if(session!==reviewSession.current)return;setReviewTotal(pending.total);reviewBuffer.current=pending.pairs
   const pair=pending.pairs.find(p=>!skippedReviews.current.has(`${p.first}:${p.second}`))
   const person=nextPeople.find(p=>p.id===pair?.first)
   if(pair&&person){setReview(person);setAnchorFaces(pair.firstFaces);setMatches([{id:pair.second,distance:pair.distance,faceIds:pair.secondFaces,previousNo:false}])}
   else{setReview(null);setMessage(pending.pending?'Your answers are saved. Groups are improving in the background; more checks appear as they finish.':pending.total?'Remaining suggestions can be reviewed later.':'Review complete. Your answers are saved.');setMatches([])}
  }catch(error){onError(String(error))}finally{if(session===reviewSession.current)setBusy(false)}
 },[onError])
 useEffect(()=>{
  if(!desktop||!active)return
  let mounted=true;let timer:ReturnType<typeof setTimeout>
  const poll=async()=>{try{const [next,progress,org,pending]=await Promise.all([desktop!.request<Person[]>('people'),desktop!.request<EngineStatus>('status'),desktop!.request<Organization>('peopleOrganization'),desktop!.request<{total:number;pending:number;errors:{id:string;error:string}[]}>('peopleReviews')]);if(mounted){setPeople(next);setStatus(progress);setOrganization(org);setReviewTotal(pending.total);if(pending.errors.length)setMessage(`A saved review needs attention: ${pending.errors[0].error}`);setLoading(false);onGroups?.(next);}}catch(error){if(mounted)onError(String(error))}if(mounted)timer=setTimeout(()=>void poll(),2500)}
  void poll();return()=>{mounted=false;reviewSession.current++;clearTimeout(timer)}
 },[active,onError,onGroups])
 const chosen=people.filter(p=>selected.includes(p.id))
 async function run(op:string,args:Record<string,unknown>,success:string) {
  setBusy(true)
  try{
   const result=await desktop!.request<{personId?:string}>(op,args)
   const next=await desktop!.request<Person[]>('people');setPeople(next);onGroups?.(next)
   if(result?.personId){const retained=next.find(p=>p.id===result.personId);if(retained)onFilter(retained)}
   setMessage(success);setDialog(null);setEditing(null);setSelected([])
  }catch(error){onError(String(error))}finally{setBusy(false)}
 }
 async function startReview(person:Person,includePrevious=false){
  setQueueReview(false)
  setReview(person);setMatches([]);setReviewed(0);setBusy(true)
  try{const [next,faces]=await Promise.all([desktop!.request<Match[]>('similarPeople',{id:person.id,revisit:includePrevious}),desktop!.request<string[]>('personFaces',{id:person.id})]);setMatches(next.filter(m=>m.distance<.4).slice(0,6));setAnchorFaces(faces)}catch(error){onError(String(error))}finally{setBusy(false)}
 }
 async function answer(same:boolean){
  if(!review||!matches[0])return
  const current=review,match=matches[0],session=reviewSession.current
  const pairKey=`${current.id}:${match.id}`
  skippedReviews.current.add(pairKey);setReviewed(n=>n+1)
  if(queueReview){
   const pair=reviewBuffer.current.find(p=>!skippedReviews.current.has(`${p.first}:${p.second}`)&&people.some(person=>person.id===p.first)&&people.some(person=>person.id===p.second))
   if(pair){setReview(people.find(p=>p.id===pair.first)!);setAnchorFaces(pair.firstFaces);setMatches([{id:pair.second,distance:pair.distance,faceIds:pair.secondFaces,previousNo:false}]);setReviewTotal(n=>Math.max(0,n-1))}
   else setBusy(true)
   try{
    await desktop!.request('reviewFeedback',{id:current.id,candidate:match.id,same})
    if(session!==reviewSession.current)return
    setMessage('Answer saved · grouping improves in the background.')
    if(!pair)await openPending(people)
   }catch(error){skippedReviews.current.delete(pairKey);setReviewed(n=>Math.max(0,n-1));onError(`Your answer was not saved. Please review this pair again. ${String(error)}`)}
   finally{if(session===reviewSession.current)setBusy(false)}
   return
  }
  setMatches(old=>old.slice(1))
  try{await desktop!.request('reviewFeedback',{id:current.id,candidate:match.id,same});setMessage('Answer saved · grouping improves in the background.')}
  catch(error){skippedReviews.current.delete(pairKey);setMatches(old=>[match,...old]);onError(String(error))}
 }
 async function openGroup(person:Person,offset=0){
  setManage(person);setBusy(true);setFaceSelection([]);setCorrection(null);setFaceOffset(offset)
  try{const result=await desktop!.request<{faces:Appearance[];total:number}>('groupAppearances',{id:person.id,offset});setAppearances(result.faces);setFaceTotal(result.total)}catch(error){onError(String(error))}finally{setBusy(false)}
 }
 async function correctFaces(){
  if(!manage||!correction)return
  setBusy(true)
  try{
   const result=await desktop!.request<{personId?:string}>('editPersonFaces',{faceIds:faceSelection,action:correction,target:moveTarget,name:newName})
   const next=await desktop!.request<Person[]>('people');setPeople(next);onGroups?.(next);setMessage('Group updated. Your correction is saved for future indexing.')
   const original=next.find(p=>p.id===manage.id);if(original)await openGroup(original);else{setManage(null);const retained=next.find(p=>p.id===result.personId);if(retained&&personId===manage.id)onFilter(retained)}
  }catch(error){onError(String(error))}finally{setBusy(false)}
 }
 const candidate=people.find(p=>p.id===matches[0]?.id),me=people.find(p=>p.isMe)
 return <section className={`people-section ${exploring?'compact-people explorer-panel':''}`} aria-label={exploring?'People explorer':'People groups'}>
  <div className="section-heading"><div><h2>People</h2><p>{loading?'Loading saved face groups…':`${people.length} people groups · Your answers improve local matching.`}</p></div>{desktop&&<div className="button-row"><button disabled={busy} onClick={()=>setDialog('refind')}>Refind faces</button><button disabled={busy} onClick={()=>void run('faceControl',{enabled:!status.enabled,retry:true},status.enabled?'Face indexing paused.':'Face indexing resumed.')}>{status.enabled?'Pause face indexing':'Resume face indexing'}</button></div>}</div>
  {!desktop?<div className="empty-state"><Icon name="people"/><strong>Open the desktop app for local face indexing</strong><p>Recognition runs on this device. Nothing is uploaded.</p></div>:<>
   <div className="face-status" aria-live="polite"><span className={`state-dot ${status.enabled&&!status.paused&&status.done<status.total?'working':''}`}/><div><strong>{loading?'Reading saved index…':status.error?'Indexing needs attention':status.allPaused?'All indexing stopped':status.paused?'Paused for viewing':!status.total?'No photos to index':status.enabled?status.state==='loading'?'Loading local face model…':status.done>=status.total?'Face index is up to date':'Finding faces':'Face indexing paused'}</strong><span>{status.done.toLocaleString()} / {status.total.toLocaleString()} photos checked{status.failed>0&&` · ${status.failed} issues`}</span>{status.error&&<small role="alert">{status.error}</small>}</div><progress value={status.done} max={Math.max(1,status.total)}/></div>
   <p className="folder-help">Close matches are organized in the background during indexing and after your reviews. Open a person to explore photos; use Edit group only when a face needs correcting. Originals are never changed.</p>
<div className="people-auto-status" role="status"><span>{organization.paused?'Organization paused · Editing groups is still available.':organization.state==='working'?`Organizing close matches · ${organization.checked} / ${organization.total} groups`:organization.state==='error'?`Organization needs attention: ${organization.error}`:organization.state==='complete'?`Organized · ${organization.merged} duplicate groups combined. Uncertain matches stay separate.`:'Automatic grouping keeps close matches together.'}</span></div>
   {message&&<p className="correction-message" role="status">{message}</p>}
   <div className="people-tools">{reviewTotal>0&&<button disabled={busy} onClick={()=>{reviewSession.current++;skippedReviews.current.clear();setReviewed(0);void openPending(people)}}>Review for accuracy · {reviewTotal}</button>}{me&&<button className="me-shortcut" onClick={()=>onFilter(me)}><FaceThumbnail id={me.faceId}/>Photos of me <span>{me.count}</span></button>}{exploring&&<button onClick={()=>{setSearch('');onReset?.()}}>All people</button>}</div>
   {chosen.length>0&&<div className="face-selection"><strong>{chosen.length} selected</strong><button disabled={busy||chosen.length<2} onClick={()=>{setTarget(chosen.find(p=>p.isMe||p.name)?.id||chosen[0].id);setDialog('same')}}>Same face</button><button disabled={busy||chosen.length!==1} onClick={()=>void run('setMe',{id:chosen[0].id},'Your person is saved. Use Photos of me anytime.')}>It’s me</button><button disabled={busy} onClick={()=>setDialog('reject')}>Not a face</button><button disabled={busy} onClick={()=>setSelected([])}>Clear selection</button></div>}
   {!!people.length&&<label className="explorer-search"><Icon name="search" size={15}/><input aria-label="Find a person" placeholder="Find a person" value={search} onChange={e=>setSearch(e.target.value)}/></label>}
   <div className="people-grid">{visiblePeople.map((person,index)=><article key={person.id} className={`person-card ${selected.includes(person.id)?'is-selected':''} ${personId===person.id?'current-person':''}`}><label className="person-select"><input type="checkbox" aria-label={`Select ${person.name||`person ${index+1}`}`} checked={selected.includes(person.id)} disabled={busy} onChange={e=>setSelected(old=>e.target.checked?[...old,person.id]:old.filter(id=>id!==person.id))}/></label><button className="person-photo" aria-label={`Show photos of ${person.name||`Person ${index+1}`}`} aria-pressed={personId===person.id} onClick={()=>onFilter(person)}><FaceThumbnail id={person.faceId}/>{exploring&&<span><strong>{person.name||`Person ${index+1}`}</strong><small>{person.count} photos</small></span>}</button><button className="person-name" onClick={()=>{setEditing(person);setName(person.name)}}>{person.name||'Add a name'}{person.isMe&&' · Me'}</button><span>{person.count.toLocaleString()} {person.count===1?'photo':'photos'}</span><div className="person-correction-actions"><button disabled={busy} onClick={()=>void openGroup(person)}>Edit group</button></div></article>)}</div>
   {filteredPeople.length>shown&&<button className="people-show-more" onClick={()=>setShown(n=>n+60)}>Show more people · {shown} of {filteredPeople.length}</button>}
   {!people.length&&<div className="empty-state"><Icon name="people"/><strong>{loading?'Loading people…':'Your people will appear here'}</strong><p>Start face indexing to find people. Names and corrections survive restart.</p></div>}
  </>}
{manage&&createPortal(<div className="edit-backdrop" role="dialog" aria-modal="true" aria-label="Edit person group"><div className="person-group-editor"><div className="section-heading"><div><h3>{manage.name||'Edit person group'}{manage.isMe?' · Me':''}</h3><p>{faceTotal} {faceTotal===1?'appearance':'appearances'} · Select only the faces that need correcting.</p></div><button disabled={busy} onClick={()=>setManage(null)} aria-label="Close group editor">×</button></div><div className="button-row"><button disabled={busy} onClick={()=>{setEditing(manage);setName(manage.name);setManage(null)}}>Name person</button><button disabled={busy||manage.isMe} onClick={()=>void run('setMe',{id:manage.id},'Your person is saved.').then(()=>setManage(null))}>It’s me</button><button disabled={busy} onClick={()=>{setManage(null);void startReview(manage)}}>Find a few similar people</button></div><div className="group-face-grid">{appearances.map(face=><label key={face.id} className={faceSelection.includes(face.id)?'selected-appearance':''}><input type="checkbox" disabled={busy} checked={faceSelection.includes(face.id)} aria-label={`Select face in ${face.name}`} onChange={e=>setFaceSelection(old=>e.target.checked?[...old,face.id]:old.filter(id=>id!==face.id))}/><FaceThumbnail id={face.id} alt={`Face in ${face.name}`}/><span title={face.name}>{face.name}</span></label>)}</div><div className="group-edit-controls"><div className="button-row"><span>{faceSelection.length} selected</span>{faceSelection.length>0&&<><button disabled={busy} onClick={()=>{setCorrection('move');setMoveTarget('')}}>Move to person</button><button disabled={busy||!faceSelection.length} onClick={()=>{setCorrection('new');setNewName('')}}>Separate person</button><button disabled={busy} onClick={()=>setCorrection('reject')}>Not a face</button></>}{!faceSelection.length&&<small>Select a face to move it, separate it, or remove a false detection.</small>}</div>{correction&&<div className="group-correction"><p>{correction==='reject'?'Remove selected detections? Their photos remain in the library.':correction==='new'?'Create a separate person from the selected faces. This also teaches the index to keep these people apart.':'Choose the person these faces belong to. Only selected appearances will move.'}</p>{correction==='move'&&<select aria-label="Move faces to person" value={moveTarget} onChange={e=>setMoveTarget(e.target.value)}><option value="">Choose a person</option>{people.filter(p=>p.id!==manage.id).map((p,i)=><option key={p.id} value={p.id}>{p.name||`Unnamed person ${i+1}`} · {p.count} photos</option>)}</select>}{correction==='new'&&<input aria-label="New person name" placeholder="Name (optional)" value={newName} maxLength={120} onChange={e=>setNewName(e.target.value)}/>}<div className="button-row"><button disabled={busy} onClick={()=>setCorrection(null)}>Cancel</button><button className="primary-action" disabled={busy||!faceSelection.length||(correction==='move'&&!moveTarget)} onClick={()=>void correctFaces()}>{busy?'Saving…':'Save correction'}</button></div></div>}{faceTotal>60&&<div className="button-row"><button disabled={busy||faceOffset===0} onClick={()=>void openGroup(manage,Math.max(0,faceOffset-60))}>Previous</button><span>{faceTotal?faceOffset+1:0}–{Math.min(faceOffset+60,faceTotal)} of {faceTotal}</span><button disabled={busy||faceOffset+60>=faceTotal} onClick={()=>void openGroup(manage,faceOffset+60)}>Next</button></div>}</div></div></div>,document.body)}
{review&&createPortal(<div className="edit-backdrop" role="dialog" aria-modal="true" aria-label="Review face groups"><div className="face-review-editor"><div className="section-heading"><div><h3>Is this the same person?</h3><p>{reviewed} reviewed · {queueReview?`${reviewTotal} suggestions available`:`${matches.length} suggestions left`}</p></div><button onClick={()=>{reviewSession.current++;setReview(null);setBusy(false)}} aria-label="Close face review">×</button></div><p>{queueReview?'Review as many as you like. Answers are saved immediately. Groups improve in the background while you continue.':'Compare the appearances, not just one photo.'} Skip whenever you are unsure.</p><div className="face-review-pair"><section><h4>{review.name||'Selected person'}{review.isMe?' · Me':''}</h4><span>{review.count} photos in this person</span><div className="face-review-examples">{anchorFaces.map(id=><FaceThumbnail key={id} id={id} alt="Selected person appearance"/>)}</div></section><section><h4>{candidate?.name||'Suggested group'}</h4><span>{candidate?`${candidate.count} photos`:busy?'Loading…':'Review complete'}</span><div className="face-review-examples">{matches[0]?.faceIds.map(id=><FaceThumbnail key={id} id={id} alt="Suggested person appearance" loading="eager"/>)}</div>{matches[0]?.previousNo&&<p>Previously marked as a different person.</p>}</section></div>{busy&&queueReview&&<p role="status">Loading more suggestions… Your saved answers are processing in the background.</p>}<div className="face-review-decisions"><button disabled={busy||!candidate} className="primary-action" onClick={()=>void answer(true)}>Yes, same person</button><button disabled={busy||!candidate} onClick={()=>void answer(false)}>No, different person</button><button disabled={busy||!candidate} onClick={()=>{if(queueReview){skippedReviews.current.add(`${review.id}:${matches[0].id}`);void openPending(people)}else setMatches(old=>old.slice(1));setReviewed(n=>n+1)}}>Skip</button></div><div className="button-row"><button onClick={()=>{reviewSession.current++;setReview(null);setBusy(false)}}>Done for now</button>{!queueReview&&<button disabled={busy} onClick={()=>void startReview(review,true)}>Revisit previous No answers</button>}<button disabled={busy} onClick={()=>{onFilter(review);setReview(null)}}>View all {review.count} photos</button></div><small>Same person combines these groups. Different person keeps them apart. No originals are changed.</small></div></div>,document.body)}
  {editing&&createPortal(<div className="edit-backdrop" role="dialog" aria-modal="true" aria-label="Name this person"><form className="person-editor" onSubmit={e=>{e.preventDefault();void run('renamePerson',{id:editing.id,name},'Name saved.')}}><FaceThumbnail id={editing.faceId}/><h3>Name this person</h3><input autoFocus aria-label="Person name" value={name} maxLength={120} onChange={e=>setName(e.target.value)}/><div className="button-row"><button disabled={busy} type="button" onClick={()=>setEditing(null)}>Cancel</button><button disabled={busy} type="submit">Save name</button></div></form></div>,document.body)}
  {dialog&&createPortal(<div className="edit-backdrop" role="dialog" aria-modal="true" aria-label={dialog==='same'?'Same person':dialog==='reject'?'Not a face':'Refind faces'}><div className="person-editor correction-editor"><h3>{dialog==='same'?'These are the same person':dialog==='reject'?'Remove false face detections':'Recheck face detection'}</h3><p>{dialog==='same'?'Choose the name and portrait to keep. Every photo from these groups is included, with duplicate photos counted once. Confirmed appearances also improve future matching.':dialog==='reject'?'Removes detections only. Originals stay untouched.':'Rechecks detection locally, keeping names, confirmed groups and rejected detections. This can take time.'}</p>{dialog!=='refind'&&<div className="merge-candidates">{chosen.map(p=><label key={p.id}><FaceThumbnail id={p.faceId}/>{dialog==='same'&&<input type="radio" name="same-target" checked={target===p.id} onChange={()=>setTarget(p.id)}/>}<span>{p.name||'Unnamed person'}<small>{p.count} photos{p.isMe?' · Me':''}</small></span></label>)}</div>}<div className="button-row"><button disabled={busy} onClick={()=>setDialog(null)}>Cancel</button><button disabled={busy} className="primary-action" onClick={()=>dialog==='same'?void run('mergePeople',{into:target,fromIds:chosen.filter(p=>p.id!==target).map(p=>p.id)},'Same person saved. All combined photos are now selected.'):dialog==='reject'?void run('rejectPeople',{ids:chosen.map(p=>p.id)},'False faces removed.'):void run('refindFaces',{},'Face recheck queued.')}>{busy?'Saving…':dialog==='same'?'Confirm same person':dialog==='reject'?'Confirm not a face':'Start recheck'}</button></div></div></div>,document.body)}
 </section>
}
