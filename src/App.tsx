import { memo, useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { cachedThumbnailUrl, retainThumbnailUrl, releaseThumbnailUrl } from './lib/thumbnailUrls'
import { useSmoothGalleryScroll } from './lib/useSmoothGalleryScroll'
import { observeThumbnail } from './lib/thumbnailObservers'
import type { MediaKind } from './lib/catalog'
import type { RuntimeMedia } from './lib/runtimeMedia'
import type { FolderRecord, LocalDirectoryHandle, PreviewRecord } from './lib/libraryStore'
import { useLibrary } from './lib/useLibrary'
import { desktop, type Person } from './lib/desktop'
import { Icon, type IconName } from './components/Icon'
import { Viewer } from './components/Viewer'
import { People } from './components/People'
import { StorageSettings } from './components/StorageSettings'
import { EngineActivity } from './components/EngineActivity'
import { useEngineStatus } from './lib/useEngineStatus'
import { Places, type PlaceGroup } from './components/Places'
import { DiskBrowser } from './components/DiskBrowser'
import { BackupWorkspace } from './components/BackupWorkspace'
import { Memories, MemoryNotice } from './components/Memories'
import { MediaSelectionToolbar } from './components/MediaSelectionToolbar'
import brandSvg from '../public/zerocloud.svg?raw'
import './App.css'
import './AppOverrides.css'
import './PriorityUI.css'
import './ProfessionalUI.css'
import './FocusUI.css'
import './ExplorerUI.css'
import './ViewerKeys.css'
import './Timeline.css'
import './ImmersiveViewer.css'
import './LibraryEnhancements.css'
import './DesignSystem.css'
import { prefetchViewerNeighbors } from './lib/viewerPrefetch'
import { categoryMatches } from './lib/mediaCategories'

type View = 'library' | 'recent' | 'favorites' | 'folders' | 'disks' | 'people' | 'places' | 'duplicates' | 'storage' | 'backup' | 'memories'
const navigation: Array<{ id: View; icon: IconName; label: string }> = [
  {id:'memories',icon:'video',label:'Memories'},
  { id: 'library', icon: 'library', label: 'Library' }, { id: 'recent', icon: 'recent', label: 'Recent' },
  { id: 'favorites', icon: 'favorites', label: 'Favorites' }, { id: 'folders', icon: 'folders', label: 'Folders' },
  { id: 'disks', icon: 'storage', label: 'Disks & folders' },
  { id: 'people', icon: 'people', label: 'People' }, { id: 'places', icon: 'places', label: 'Places' }, { id: 'duplicates', icon: 'duplicates', label: 'Duplicates' },
  { id:'backup', icon:'backup', label:'Backup & protect' },
]
const labels: Record<MediaKind, string> = { image: 'Images', raw: 'RAW', video: 'Videos', audio: 'Audio', document: 'Documents', other: 'Other' }
const thumbnailTask = (item: RuntimeMedia) => item.kind === 'image' || item.kind === 'raw' || item.kind === 'video'

function App() {
  useSmoothGalleryScroll()
  const library = useLibrary()
  const { pauseForViewer } = library
  const input = useRef<HTMLInputElement>(null)
  const reconnectId = useRef<string | undefined>(undefined)
  const [view, setView] = useState<View>('library')
  const [settingsTab, setSettingsTab] = useState<'storage' | 'activity'>('storage')
  const engineStatus = useEngineStatus(library.indexingStopped)
  const [filter, setFilter] = useState<'all' | MediaKind>('all')
  const [folderFilter, setFolderFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [dateYear, setDateYear] = useState('all')
  const [dateOrder, setDateOrder] = useState('newest')
  const [category, setCategory] = useState('all')
  const [addingSource, setAddingSource] = useState(false)
  const sourceDialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { if(addingSource && !sourceDialog.current?.open)sourceDialog.current?.showModal() },[addingSource])
  const [selectionMode, setSelectionMode] = useState(false)
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const [selectionBusy, setSelectionBusy] = useState(false)
  const toggleSelection = useCallback((id: string) => setSelection(old => { const next=new Set(old); if(next.has(id))next.delete(id);else if(next.size<100)next.add(id);return next }), [])
  async function selectionAction(action: 'deleteMedia' | 'shareMedia') {
    setSelectionBusy(true)
    try { if(await library.selectionAction([...selection],action))setSelection(new Set()) } finally {setSelectionBusy(false)}
  }
  const [theme, setTheme] = useState(() => localStorage.getItem('zerocloud-theme') || 'system')
  useEffect(() => {
    const preference = matchMedia('(prefers-color-scheme: dark)')
    const apply = () => { document.documentElement.dataset.theme = theme === 'system' ? preference.matches ? 'dark' : 'light' : theme }
    apply(); localStorage.setItem('zerocloud-theme', theme); preference.addEventListener('change', apply)
    return () => preference.removeEventListener('change', apply)
  }, [theme])
  const years = useMemo(() => [...new Set(library.items.flatMap(item=>item.captureDate?[item.captureDate.slice(0,4)]:[]))].sort().reverse(),[library.items])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [viewerReady,setViewerReady]=useState('')
  const [memoryPlaying, setMemoryPlaying] = useState(false)
  const [personFilter, setPersonFilter] = useState<Person | null>(null)
  const [placeFilter, setPlaceFilter] = useState<PlaceGroup | null>(null)
  const peopleScroll = useRef(0)
  const placesScroll = useRef(0)
  const personIds = useMemo(() => personFilter ? new Set(personFilter.mediaIds) : placeFilter ? new Set(placeFilter.mediaIds) : null, [personFilter, placeFilter])
  const updatePeople = useCallback((people: Person[]) => setPersonFilter((previous) => {
    if (!previous) return null
    const next = people.find(person => person.id === previous.id || person.aliases?.includes(previous.id))
    if (!next) return null
    return next.id === previous.id && next.name === previous.name && next.isMe === previous.isMe && next.faceId === previous.faceId && next.mediaIds.length === previous.mediaIds.length && next.mediaIds.every((id,index) => id === previous.mediaIds[index]) ? previous : next
  }), [])
  const updatePlaces = useCallback((places: PlaceGroup[]) => setPlaceFilter((previous) => previous ? places.find((place) => place.id === previous.id) ?? null : null), [])
  const [collapsed, setCollapsed] = useState(() => { try { return window.innerWidth <= 560 || localStorage.getItem('vela-rail') === 'collapsed' } catch { return window.innerWidth <= 560 } })
  const selected = library.items.find((item) => item.id === selectedId)
  const queueCurrent = library.queueState.current
  useEffect(() => {
    if (!desktop) return
    let touched = Date.now()
    const touch = () => { touched = Date.now() }
    const pulse = () => void desktop!.request('memory:activity', {busy:!!selectedId || memoryPlaying || (!!library.scan && !library.indexingStopped) || (!!queueCurrent && !library.indexingStopped) || Date.now()-touched < 20000}).catch(()=>{})
    window.addEventListener('pointerdown',touch); window.addEventListener('keydown',touch); window.addEventListener('wheel',touch,{passive:true})
    pulse(); const timer=setInterval(pulse,5000)
    return () => {clearInterval(timer);window.removeEventListener('pointerdown',touch);window.removeEventListener('keydown',touch);window.removeEventListener('wheel',touch)}
  },[selectedId,memoryPlaying,library.scan,queueCurrent,library.indexingStopped])
  const summary = useMemo(() => {
    const kinds = { image: 0, raw: 0, video: 0, audio: 0, document: 0, other: 0 }
    let ready = 0; let failed = 0; let total = 0
    const counts = new Map<string, number>()
    for (const item of library.items) {
      kinds[item.kind]++; if (thumbnailTask(item)) { total++; if (item.thumbnailReady) ready++; if (item.processing === 'failed') failed++ }
      const signature = `${item.name.toLowerCase()}:${item.byteSize}`; counts.set(signature, (counts.get(signature) ?? 0) + 1)
    }
    const duplicateIds = new Set(library.items.filter((item) => counts.get(`${item.name.toLowerCase()}:${item.byteSize}`)! > 1).map((item) => item.id))
    return { kinds, ready, failed, total, duplicateIds }
  }, [library.items])
  const visible = useMemo(() => {
    const search = query.trim().toLowerCase()
    const result = library.items.filter((item) => (view !== 'favorites' || item.favorite) && (view !== 'duplicates' || summary.duplicateIds.has(item.id))
      && (filter === 'all' || item.kind === filter) && (folderFilter === 'all' || item.folderId === folderFilter)
      && (!personIds || personIds.has(item.id))
      && categoryMatches(item, category)
      && (dateYear === 'all' || (dateYear === 'unknown' ? !item.captureDate : item.captureDate?.startsWith(dateYear)))
      && (!dateFrom || (!!item.captureDate && item.captureDate >= dateFrom)) && (!dateTo || (!!item.captureDate && item.captureDate <= dateTo))
      && (!search || `${item.name} ${item.relativePath} ${item.extension} ${item.date}`.toLowerCase().includes(search)))
    return result.sort((a,b)=>{
      if(!a.captureDate&&!b.captureDate)return a.name.localeCompare(b.name)||a.id.localeCompare(b.id)
      if(!a.captureDate)return 1;if(!b.captureDate)return -1
      const comparison=(a.capturedAt??a.captureDate).localeCompare(b.capturedAt??b.captureDate)
      return (dateOrder==='oldest'?comparison:-comparison)||a.name.localeCompare(b.name)||a.id.localeCompare(b.id)
    })
  }, [library.items, summary.duplicateIds, query, view, filter, folderFilter, personIds,dateFrom,dateTo,dateYear,dateOrder,category])
  const viewerItems = useMemo(() => visible.filter((item) => ['image', 'raw', 'video'].includes(item.kind)), [visible])
  const viewerSequence = useRef(viewerItems)
  useEffect(() => { viewerSequence.current = viewerItems }, [viewerItems])
  const viewerIndex = viewerItems.findIndex((item) => item.id === selectedId)
  useEffect(() => {
    if (viewerIndex < 0 || library.indexingStopped || viewerReady!==selectedId) return
    return prefetchViewerNeighbors(viewerItems.slice(viewerIndex + 1, viewerIndex + 2).concat(viewerItems.slice(Math.max(0, viewerIndex - 1), viewerIndex)), library.prefetch)
  }, [viewerIndex, viewerItems, library.prefetch, library.indexingStopped,viewerReady,selectedId])
  const navigateViewer = useCallback((direction: -1 | 1, videosOnly = false) => {
    setViewerReady('')
    const sequence = videosOnly ? viewerSequence.current.filter((item) => item.kind === 'video') : viewerSequence.current
    setSelectedId((current) => {
      const index = sequence.findIndex((item) => item.id === current)
      return index < 0 ? current : sequence[index + direction]?.id ?? current
    })
  }, [])

  useEffect(() => { try { localStorage.setItem('vela-rail', collapsed ? 'collapsed' : 'expanded') } catch { /* Storage warning is handled by the catalog. */ } }, [collapsed])
  useEffect(() => {
    const narrow = window.matchMedia('(max-width: 560px)')
    const resize = () => { if (narrow.matches) setCollapsed(true) }
    narrow.addEventListener('change', resize)
    return () => narrow.removeEventListener('change', resize)
  }, [])
  function navigate(next: View) {
    setView(next); setFilter('all'); setFolderFilter('all'); setQuery(''); setPersonFilter(null); setPlaceFilter(null)
    setDateFrom('');setDateTo('');setDateYear('all');setCategory('all')
    setSelectionMode(false);setSelection(new Set())
    if (window.innerWidth <= 560) setCollapsed(true)
    window.scrollTo({ top: 0, behavior: 'instant' })
  }
  const returnToGroups = useCallback(() => {
    const next = personFilter ? 'people' : 'places'
    const scroll = personFilter ? peopleScroll.current : placesScroll.current
    setSelectedId(null); pauseForViewer(false); setPersonFilter(null); setPlaceFilter(null); setView(next); setQuery(''); setFilter('all'); setFolderFilter('all')
    requestAnimationFrame(() => window.scrollTo({top:scroll,behavior:'instant'}))
  }, [personFilter, pauseForViewer])
  useEffect(() => {
    const back = () => { if (personFilter || placeFilter) returnToGroups() }
    window.addEventListener('popstate',back);return()=>window.removeEventListener('popstate',back)
  },[personFilter,placeFilter,returnToGroups])
  function backToGroups() { if (history.state?.velaGroup) history.back(); else returnToGroups() }
  function filterPerson(person: Person) { if (!personFilter) { peopleScroll.current = window.scrollY; history.pushState({velaGroup:'people'},'') }; setView('people'); setPersonFilter(person); setPlaceFilter(null); setQuery(''); setFilter('all'); setFolderFilter('all'); setCategory('all'); setDateFrom(''); setDateTo(''); setDateYear('all'); window.scrollTo({top:0,behavior:'instant'}) }
  function filterPlace(place: PlaceGroup) { if (!placeFilter) { placesScroll.current = window.scrollY; history.pushState({velaGroup:'places'},'') }; setView('places'); setPlaceFilter(place); setPersonFilter(null); setQuery(''); setFilter('all'); setFolderFilter('all'); window.scrollTo({top:0,behavior:'instant'}) }
  async function chooseFolder() {
    if (library.scan) return
    if (desktop) { navigate('library'); await library.nativeFolder(); return }
    const picker = (window as Window & { showDirectoryPicker?: (options: { mode: 'read' }) => Promise<LocalDirectoryHandle> }).showDirectoryPicker
    if (picker) {
      try {
        const handle = await picker({ mode: 'read' })
        const id = reconnectId.current; reconnectId.current = undefined
        await library.addHandle(handle, id); navigate('library'); return
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') { reconnectId.current = undefined; return }
        if (!(error instanceof DOMException && (error.name === 'SecurityError' || error.name === 'NotSupportedError'))) { library.setNotice(String(error)); return }
      }
    }
    input.current?.setAttribute('webkitdirectory', '')
    input.current?.setAttribute('directory', '')
    input.current?.click()
  }
  async function importFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []); event.target.value = ''
    const id = reconnectId.current; reconnectId.current = undefined
    await library.addFiles(files, id); navigate('library')
  }
  async function reconnect(folder: FolderRecord) {
    if (desktop) { await library.nativeFolder(folder, true); return }
    if (!await library.reconnect(folder)) { reconnectId.current = folder.id; void chooseFolder() }
  }
  async function chooseCache(folder: FolderRecord) {
    try { if (await desktop!.request('chooseFolderCache', {folderId:folder.id})) await library.nativeFolder(folder) }
    catch(error) { library.setNotice(String(error)) }
  }
  async function syncCaches() {
    try { const result = await desktop!.request<{error:string}>('syncFolderCaches'); library.setNotice(result.error || 'Portable caches are synced. Stop indexing and close ZeroCloud before disconnecting the drive.') }
    catch(error) { library.setNotice(String(error)) }
  }
  const openMedia = useCallback((item: RuntimeMedia) => { setViewerReady('');pauseForViewer(true); setSelectedId(item.id) }, [pauseForViewer])
  const closeViewer = useCallback(() => { setSelectedId(null); pauseForViewer(false) }, [pauseForViewer])
  const title = view === 'storage' ? 'Settings' : navigation.find((entry) => entry.id === view)?.label ?? 'Library'
  const phase = library.loading ? 'Restoring your library' : library.scan || (selected ? 'Background paused for viewing' : library.queueState.current ? `Preparing ${library.queueState.current}` : library.queueState.paused ? 'Background paused' : library.queueState.pending ? 'Preview work queued' : 'Library ready')
  const needsAttention = !!(summary.failed || engineStatus?.cacheError || engineStatus?.error || engineStatus?.failed || engineStatus?.locations?.failed)
  const working = !!(library.scan || library.queueState.pending || library.categoryProgress?.state==='Categorizing previews' || (engineStatus?.enabled && engineStatus.done < engineStatus.total) || (engineStatus?.locations && engineStatus.locations.done < engineStatus.locations.total) || (library.captureProgress && library.captureProgress.done < library.captureProgress.total))
  const activityLabel = library.loading || (desktop && !engineStatus) ? 'Loading library' : library.indexingStopped ? 'Indexing stopped' : engineStatus?.paused ? 'Paused for viewing' : working ? 'Indexing' : needsAttention ? 'Needs attention' : 'Up to date'
  function openActivity() { navigate('storage'); setSettingsTab('activity') }
  const navButton = (id: View) => {
    const entry = navigation.find((item) => item.id === id)!
    return <button key={id} title={entry.label} aria-label={entry.label} aria-current={view === id && (id !== 'library' || filter === 'all') ? 'page' : undefined} className={`nav-item ${view === id && (id !== 'library' || filter === 'all') ? 'active' : ''}`} onClick={() => navigate(id)}><span className="nav-icon"><Icon name={entry.icon} /></span><span>{entry.label}</span></button>
  }

  return <div className={`app-shell ${collapsed ? 'rail-collapsed' : ''}`}>
    <aside className="sidebar" aria-label="Library navigation">
      <div className="brand-row"><div className="brand"><img className="brand-icon" src={`data:image/svg+xml,${encodeURIComponent(brandSvg)}`} alt="" /><span className="brand-name">ZeroCloud</span></div><button className="sidebar-toggle" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'} title={collapsed ? 'Expand navigation' : 'Collapse navigation'}><Icon name="menu" /></button></div>
      <button className="add-source" title="Add folder or disk" aria-label="Add folder or disk" onClick={() => setAddingSource(true)} disabled={library.loading || !!library.scan}><Icon name="plus" /><span className="add-label">Add folder or disk</span></button>
      <nav>
        <div className="nav-group" role="group" aria-label="Browse"><p className="nav-label">Browse</p>{['library', 'recent', 'favorites'].map((id) => navButton(id as View))}</div>
        <div className="nav-group" role="group" aria-label="Explore"><p className="nav-label">Explore</p>{['people', 'places', 'memories'].map((id) => navButton(id as View))}</div>
        <div className="nav-group" role="group" aria-label="Manage"><p className="nav-label">Manage</p>{['folders', 'disks', 'duplicates', 'backup'].map((id) => navButton(id as View))}</div>
      </nav>
      <div className="sidebar-footer"><button className={`nav-item ${view === 'storage' ? 'active' : ''}`} title="Settings" aria-label="Settings" aria-current={view === 'storage' ? 'page' : undefined} onClick={() => { navigate('storage'); setSettingsTab('storage') }}><span className="nav-icon"><Icon name="settings" /></span><span>Settings</span></button><small>{desktop ? 'Private · On-device' : 'Private · Browser library'}</small></div>
    </aside>
    <main className="content">
      <div hidden={view!=='backup'}><BackupWorkspace active={view==='backup'} onAddVault={(path)=>{navigate('library');void library.nativeFolder(undefined,false,path)}} /></div>
      {view !== 'backup' && <>
      <header className="topbar"><label className="searchbox"><span><Icon name="search" /></span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search media…" aria-label="Search media" /></label><button className={`activity-chip ${working && !library.indexingStopped ? 'working' : ''} ${needsAttention ? 'attention' : ''} ${library.indexingStopped ? 'stopped' : ''}`} aria-label={`Indexing activity: ${activityLabel}`} title={`${activityLabel}${needsAttention ? ' · Some items need attention' : ''} · ${summary.ready.toLocaleString()} / ${summary.total.toLocaleString()} previews · Open indexing details`} onClick={openActivity}><span className="activity-dot" /><span>{activityLabel}</span><Icon name="chevron" size={13} /></button><span className="local-badge">Local</span></header>
      {library.notice && <div className="library-notice" role="alert">{library.notice}<button onClick={() => library.setNotice('')} aria-label="Dismiss message">×</button></div>}
      <MemoryNotice onOpen={() => navigate('memories')} />
      {library.captureProgress && ((category==='date' && ['library','recent','favorites','duplicates'].includes(view) && library.captureProgress.done<library.captureProgress.total)||(view==='storage'&&settingsTab==='activity')) && <div className="capture-index-status" role="status">Capture dates · {library.captureProgress.done.toLocaleString()} / {library.captureProgress.total.toLocaleString()} checked · {library.captureProgress.dated.toLocaleString()} with metadata · {library.captureProgress.state}{library.captureProgress.state==='Paused'&&' — resume indexing in Settings to continue'}</div>}
      {(['library','recent','favorites','duplicates'].includes(view)||personFilter||placeFilter)&&<div className="library-category-dock">
        <div className="category-menu-row"><label>Explore <select aria-label="Library category" value={category} onChange={e=>{setCategory(e.target.value);if(e.target.value!=='date'){setDateYear('all');setDateFrom('');setDateTo('')}}}><option value="all">Everything</option><option value="date">Date</option><option value="screenshots">Screenshots</option><option value="landscapes">Landscapes & nature</option><option value="beaches">Beaches & coast</option><option value="animals">Animals & pets</option><option value="architecture">Architecture</option><option value="plants">Plants & flowers</option><option value="food">Food & cafés</option><option value="documents">Documents & receipts</option></select></label><button className="select-mode-toggle" aria-pressed={selectionMode} disabled={selectionBusy} onClick={()=>{setSelectionMode(!selectionMode);setSelection(new Set())}}>{selectionMode?'Done':'Select'}</button><button aria-label="Change appearance" title="Change appearance" onClick={()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark')}>{theme==='dark'?'☀':'☾'}</button></div>
        <div className="filter-row"><button className={filter==='all'?'selected':''} onClick={()=>setFilter('all')}>All media <b>{library.items.length}</b></button>{(Object.keys(labels) as MediaKind[]).filter(kind=>summary.kinds[kind]).map(kind=><button key={kind} className={filter===kind?'selected':''} onClick={()=>setFilter(kind)}>{labels[kind]} <b>{summary.kinds[kind]}</b></button>)}</div>
        {selectionMode&&<MediaSelectionToolbar count={selection.size} busy={selectionBusy} native={!!desktop} onSelectAll={()=>setSelection(new Set(visible.filter(thumbnailTask).slice(0,100).map(item=>item.id)))} onClear={()=>setSelection(new Set())} onAction={selectionAction} onCategory={tag=>library.assignCategory([...selection],tag)}/>} 
        {category==='date'&&<section className="timeline-filters" aria-label="Capture date filters"><label>Year<select aria-label="Capture year" value={dateYear} onChange={e=>setDateYear(e.target.value)}><option value="all">All years</option>{years.map(year=><option key={year}>{year}</option>)}<option value="unknown">Unknown capture date</option></select></label><label>From<input type="date" aria-label="Captured from" value={dateFrom} max={dateTo||undefined} onChange={e=>setDateFrom(e.target.value)}/></label><label>To<input type="date" aria-label="Captured to" value={dateTo} min={dateFrom||undefined} onChange={e=>setDateTo(e.target.value)}/></label><label>Order<select aria-label="Capture date order" value={dateOrder} onChange={e=>setDateOrder(e.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></label><button onClick={()=>{setDateYear('all');setDateFrom('');setDateTo('');setFilter('all');setFolderFilter('all');setQuery('')}}>Clear filters</button><small>Camera dates, not file-copy dates. Missing metadata is grouped separately.</small></section>}
        {!['all','date'].includes(category)&&<small className="category-help">Offline visual suggestions + file names · {library.categoryProgress?.done.toLocaleString()??0} / {library.categoryProgress?.total.toLocaleString()??0} classified · {library.categoryProgress?.state??'Loading'} · Select media to correct a category.</small>}
      </div>}
      {engineStatus?.cacheError && view !== 'storage' && <div className="library-notice" role="alert">{engineStatus.cacheError}<button onClick={openActivity}>View activity</button></div>}
      <div hidden={view !== 'people'} className={`explore-workspace ${personFilter ? 'is-exploring' : ''}`}>
        <People active={view === 'people'} exploring={!!personFilter} personId={personFilter?.id} onReset={backToGroups} onGroups={updatePeople} onError={library.setNotice} onFilter={filterPerson} />
        {view === 'people' && personFilter && <section className="explore-results" aria-label="Photos of selected person"><div className="section-heading"><div><h2>{personFilter.name || 'Selected person'}</h2><p>{visible.length.toLocaleString()} matching {visible.length === 1 ? 'photo' : 'photos'} · Choose another person in the explorer.</p></div><button className="plain-action" onClick={backToGroups}>← Back to People</button></div>{visible.length ? <VirtualGallery key={personFilter.id} items={visible} onOpen={openMedia} selectionMode={selectionMode} selection={selection} onToggleSelection={toggleSelection} onFavorite={library.favorite} thumbnail={library.visibleThumbnail} prioritize={library.prioritize} /> : <div className="empty-state"><strong>No matching photos</strong><p>Clear your search, or choose another person.</p></div>}</section>}
      </div>
      <div hidden={view !== 'places'} className={`explore-workspace ${placeFilter ? 'is-exploring' : ''}`}>
        <Places active={view === 'places'} exploring={!!placeFilter} placeId={placeFilter?.id} onReset={backToGroups} onGroups={updatePlaces} onError={library.setNotice} onFilter={filterPlace} />
        {view === 'places' && placeFilter && <section className="explore-results" aria-label="Photos of selected location"><div className="section-heading"><div><h2>Photos near {placeFilter.id}</h2><p>{visible.length.toLocaleString()} matching {visible.length === 1 ? 'photo' : 'photos'} · Choose another marker or location.</p></div><button className="plain-action" onClick={backToGroups}>← Back to Places</button></div>{visible.length ? <VirtualGallery key={placeFilter.id} items={visible} onOpen={openMedia} selectionMode={selectionMode} selection={selection} onToggleSelection={toggleSelection} onFavorite={library.favorite} thumbnail={library.visibleThumbnail} prioritize={library.prioritize} /> : <div className="empty-state"><strong>No matching photos</strong><p>Clear your search, or choose another location.</p></div>}</section>}
      </div>
      {view === 'folders' ? <section className="folder-section"><div className="section-heading"><h2>Saved folders</h2><div className="button-row">{desktop && <button onClick={() => void syncCaches()}>Sync portable caches</button>}<button className="plain-action" onClick={() => setAddingSource(true)}>Add source ＋</button></div></div><p className="folder-help">Link any media folder. When adding it, optionally save its cache on the same drive or choose a cache folder. On another computer add the originals and choose their existing portable cache to restore names, favorites, face vectors, GPS, and thumbnails.</p>{library.folders.map((folder) => <article className="folder-row" key={folder.id}><span className="folder-symbol"><Icon name="folders" size={25} /></span><div><strong>{folder.name}</strong><small>{folder.count.toLocaleString()} files · {folder.online === false ? 'Originals offline · Relink needed' : 'Linked original files'}{folder.location && ` · ${folder.location}`}</small><small>{folder.cacheLocation ? `Portable cache: ${folder.cacheLocation}` : 'Cache: app library storage'}</small></div><div className="button-row"><button disabled={!!library.scan} onClick={() => { setFolderFilter(folder.id); setPersonFilter(null); setPlaceFilter(null); setView('library') }}>View</button>{desktop && <><button disabled={!!library.scan} onClick={() => void library.nativeFolder(folder)}>Rescan</button><button disabled={!!library.scan} onClick={() => void chooseCache(folder)}>Portable cache</button></>}<button disabled={!!library.scan} onClick={() => void reconnect(folder)}>{desktop ? 'Relink folder' : 'Reconnect / rescan'}</button></div></article>)}<p className="folder-help">Portable caches save only this folder’s catalog and small derivatives, not original copies. Updates sync in batches and when ZeroCloud closes; sync and close before removing a drive.</p></section>
      : view === 'storage' ? <section className="settings-workspace"><div className="settings-heading"><h1>Settings</h1><p>Storage, saved workspaces and on-device processing.</p></div><label className="appearance-setting">Appearance<select aria-label="Theme" value={theme} onChange={e=>setTheme(e.target.value)}><option value="system">Use system theme</option><option value="light">Light</option><option value="dark">Dark</option></select></label><div className="settings-tabs" role="tablist" aria-label="Settings sections" onKeyDown={(event) => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 'storage' : event.key === 'End' ? 'activity' : settingsTab === 'storage' ? 'activity' : 'storage'; setSettingsTab(next); requestAnimationFrame(() => document.getElementById(`${next}-tab`)?.focus()) }}><button id="storage-tab" role="tab" tabIndex={settingsTab === 'storage' ? 0 : -1} aria-selected={settingsTab === 'storage'} aria-controls="storage-panel" onClick={() => setSettingsTab('storage')}>Storage & workspace</button><button id="activity-tab" role="tab" tabIndex={settingsTab === 'activity' ? 0 : -1} aria-selected={settingsTab === 'activity'} aria-controls="activity-panel" onClick={() => setSettingsTab('activity')}>Indexing & activity</button></div>{settingsTab === 'storage' ? <div id="storage-panel" role="tabpanel" aria-labelledby="storage-tab"><StorageSettings onError={library.setNotice} onStop={library.stopIndexing} /></div> : <div id="activity-panel" role="tabpanel" aria-labelledby="activity-tab" className="activity-panel"><div className="section-heading"><div><h2>Library activity</h2><p>{library.folders.length} saved folders · Progress is saved on this device.</p></div></div><section className="stats-grid" aria-label="Library summary"><article><div className="stat-icon lilac">▧</div><div><strong>{library.items.length.toLocaleString()}</strong><span>Saved media files</span></div></article><article><div className="stat-icon mint">◉</div><div><strong>{summary.ready.toLocaleString()}</strong><span>Ready previews</span></div></article><article><div className="stat-icon amber">▷</div><div><strong>{summary.kinds.video.toLocaleString()}</strong><span>Videos in original quality</span></div></article></section><section className="job-panel" aria-label="Preview processing"><div><strong>{phase}</strong><span>{summary.failed ? `${summary.failed} preview issues · ` : ''}{library.queueState.pending.toLocaleString()} queued · Open item → visible items → nearby items → background</span></div><div className="job-progress"><i style={{ width: `${summary.total ? summary.ready / summary.total * 100 : 0}%` }} /></div><small>{summary.ready.toLocaleString()} / {summary.total.toLocaleString()}</small></section><EngineActivity status={engineStatus} onPeople={() => navigate('people')} onPlaces={() => navigate('places')} stopped={library.indexingStopped} onStop={(value) => void library.stopIndexing(value)} /></div>}</section>
      : view === 'disks' ? <DiskBrowser onError={library.setNotice} onAdd={(path) => { navigate('library'); void library.nativeFolder(undefined,false,path) }} />
      : view === 'memories' ? <Memories onPlaying={setMemoryPlaying} />
      : view === 'people' ? null
      : view === 'places' ? null
      : <><section className="library-toolbar">{library.folders.length > 1 && <select aria-label="Filter by folder" value={folderFilter} onChange={(event) => setFolderFilter(event.target.value)}><option value="all">All folders</option>{library.folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select>}</section><section className="library-section"><div className="section-heading"><div><h2>{view === 'library' ? 'All media' : title}</h2><p>{visible.length.toLocaleString()} items{view === 'duplicates' ? ' · Matching names and sizes; review before treating as duplicates.' : ' · Originals open on demand.'}</p></div><button className="plain-action" onClick={() => setAddingSource(true)}>Add source ＋</button></div>{visible.length ? <VirtualGallery items={visible} onOpen={openMedia} selectionMode={selectionMode} selection={selection} onToggleSelection={toggleSelection} onFavorite={library.favorite} thumbnail={library.visibleThumbnail} prioritize={library.prioritize} /> : <div className="empty-state"><strong>{library.loading ? 'Restoring your saved library…' : view === 'favorites' ? 'No favorites yet' : 'No media found'}</strong><p>{view === 'favorites' ? 'Use the heart on a media card to save it here.' : 'Add a folder, or adjust your filters.'}</p><button disabled={library.loading} onClick={() => setAddingSource(true)}>Add folder</button></div>}</section></>}
      </>}
    </main>
    <input ref={input} className="folder-input" type="file" multiple {...{ webkitdirectory: '', directory: '' }} onChange={(event) => void importFiles(event)} />
    {addingSource && <dialog ref={sourceDialog} className="source-dialog" aria-label="Add folder or disk" onCancel={e=>{e.preventDefault();setAddingSource(false)}}><div className="person-editor source-editor"><h3>Add to your library</h3><p>Link a folder or explore an entire drive. Originals stay in place, with an optional portable cache.</p><div className="button-row"><button autoFocus onClick={()=>{setAddingSource(false);void chooseFolder()}}><Icon name="folders"/>Choose folder</button><button disabled={!desktop} onClick={()=>{setAddingSource(false);navigate('disks')}}><Icon name="storage"/>Choose disk</button><button onClick={()=>setAddingSource(false)}>Cancel</button></div></div></dialog>}
    {selected && <Viewer media={selected} open={library.open} onReady={setViewerReady} onClose={closeViewer} onFavorite={() => void library.favorite(selected)} onNavigate={navigateViewer} hasPrevious={viewerIndex > 0} hasNext={viewerIndex >= 0 && viewerIndex < viewerItems.length - 1} position={viewerIndex >= 0 ? `${viewerIndex + 1} / ${viewerItems.length}` : ''} />}
  </div>
}

type GalleryProps = { items: RuntimeMedia[]; onOpen: (item: RuntimeMedia) => void; onFavorite: (item: RuntimeMedia) => Promise<void>; thumbnail: (item: RuntimeMedia, priority: number) => Promise<PreviewRecord>; prioritize: (item: RuntimeMedia, priority: number) => void; selectionMode?:boolean; selection?:Set<string>; onToggleSelection?:(id:string)=>void }
function VirtualGallery({ items, ...actions }: GalleryProps) {
  const { thumbnail, prioritize } = actions
  const root = useRef<HTMLDivElement>(null)
  const scrollIntent = useRef({ offset: 0, direction: 1, warmed: '' })
  const [columns,setColumns]=useState(4)
  const [windowState,setWindowState]=useState({start:0,end:12,label:''})
  const rows=useMemo(()=>{
    const result:Array<{key:string;top:number;height:number;label:string;count?:number;items?:RuntimeMedia[]}>=[]
    let top=0,index=0
    while(index<items.length){
      const day=items[index].captureDate??'unknown',group:RuntimeMedia[]=[]
      while(index<items.length&&(items[index].captureDate??'unknown')===day)group.push(items[index++])
      const label=day==='unknown'?'Unknown capture date':new Date(`${day}T12:00:00`).toLocaleDateString(undefined,{weekday:'short',year:'numeric',month:'long',day:'numeric'})
      result.push({key:`date-${day}`,top,height:44,label,count:group.length});top+=44
      for(let i=0;i<group.length;i+=columns){result.push({key:`${day}-${i}`,top,height:224,label,items:group.slice(i,i+columns)});top+=224}
    }
    return result
  },[items,columns])
  const totalHeight=rows.length?rows[rows.length-1].top+rows[rows.length-1].height:0
  useEffect(() => {
    let frame = 0
    const find=(offset:number)=>{let lo=0,hi=rows.length;while(lo<hi){const mid=(lo+hi)>>>1;if(rows[mid].top+rows[mid].height<offset)lo=mid+1;else hi=mid}return lo}
    const measure = () => {
      frame = 0; if (!root.current) return
      const bounds = root.current.getBoundingClientRect()
      const nextColumns=Math.max(1,Math.floor((bounds.width+15)/179));if(nextColumns!==columns){setColumns(nextColumns);return}
      const offset=Math.max(0,-bounds.top),visible=find(offset)
      const intent=scrollIntent.current
      if(Math.abs(offset-intent.offset)>2)intent.direction=offset>=intent.offset?1:-1
      intent.offset=offset
      const edge=find(intent.direction>0?offset+innerHeight:offset)
      const warmKey=`${edge}:${intent.direction}`
      if(intent.warmed!==warmKey){
        intent.warmed=warmKey
        const ahead=intent.direction>0?rows.slice(edge,edge+4):rows.slice(Math.max(0,edge-4),edge+1).reverse()
        for(const item of ahead.flatMap(row=>row.items??[]).slice(0,24)){
          if(!thumbnailTask(item))continue
          prioritize(item,80)
          const key=`${item.id}@${item.version}`
          void retainThumbnailUrl(key,()=>thumbnail(item,80)).then(()=>releaseThumbnailUrl(key)).catch(()=>{})
        }
      }
      const label=bounds.top<innerHeight&&bounds.bottom>0?rows[visible]?.label??'':''
      setWindowState(old=>{
        const inside=rows[old.start]?.top<=Math.max(0,offset-150)&&(rows[old.end-1]?.top??0)>=offset+innerHeight+150
        if(inside&&old.label===label)return old
        return {start:find(Math.max(0,offset-(intent.direction<0?1600:400))),end:Math.min(rows.length,find(offset+innerHeight+(intent.direction>0?1600:400))+1),label}
      })
    }
    const update = () => { if (!frame) frame = requestAnimationFrame(measure) }
    const observer = new ResizeObserver(update); if (root.current) observer.observe(root.current)
    window.addEventListener('scroll', update, { passive: true }); window.addEventListener('resize', update); measure()
    return () => { observer.disconnect(); window.removeEventListener('scroll', update); window.removeEventListener('resize', update); if (frame) cancelAnimationFrame(frame) }
  }, [rows,columns,thumbnail,prioritize])
  return <div ref={root} className="virtual-gallery" style={{height:totalHeight}} role="list" aria-label="Media library">{rows.slice(windowState.start,windowState.end).map(row=>row.items?<div className="media-grid virtual-window timeline-row" key={row.key} style={{top:row.top,height:row.height,gridTemplateColumns:`repeat(${columns},minmax(0,1fr))`}}>{row.items.map(item=><MediaCard key={`${item.id}@${item.version}`} item={item} {...actions}/>)}</div>:<h3 key={row.key} className="timeline-day timeline-row" style={{top:row.top}}>{row.label}<small>{row.count} items</small></h3>)}{windowState.label&&<div className="timeline-position" aria-hidden="true">{windowState.label}</div>}</div>
}

const MediaCard = memo(function MediaCard({ item, onOpen, onFavorite, thumbnail, prioritize, selectionMode, selection, onToggleSelection }: { item: RuntimeMedia } & Omit<GalleryProps, 'items'>) {
  const root = useRef<HTMLElement>(null)
  const cacheKey = `${item.id}@${item.version}`
  const [url, setUrl] = useState(() => cachedThumbnailUrl(cacheKey))
  useEffect(() => {
    if (!root.current || !thumbnailTask(item)) return
    let active = true; let requested = false; let currentUrl = ''
    const load = (priority: number) => {
      prioritize(item, priority)
      if (requested) return
      requested = true
      void retainThumbnailUrl(cacheKey, () => thumbnail(item, priority)).then((readyUrl) => { if (active) { currentUrl = readyUrl; setUrl(readyUrl) } else releaseThumbnailUrl(cacheKey) }).catch(() => {})
    }
    const stopObserving = observeThumbnail(root.current, (near, visible) => { if (near || visible) load(visible ? 100 : 30); else prioritize(item, 1) })
    return () => { active = false; stopObserving(); prioritize(item, 1); if (currentUrl) releaseThumbnailUrl(cacheKey) }
    // Changes to progress/favorite should not recreate decoded thumbnail URLs.
  // Metadata used by this effect is stable until the source version changes.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [item.id, item.version, thumbnail, prioritize])
  const illustration = item.kind === 'video' ? '▶' : item.kind === 'audio' ? '♪' : item.kind === 'document' ? '▤' : item.kind === 'raw' ? 'RAW' : '◌'
  return <article ref={root} className="media-card" role="listitem">{selectionMode&&thumbnailTask(item)&&<label className="media-selection"><input type="checkbox" aria-label={`Select ${item.name}`} checked={selection?.has(item.id)??false} onChange={()=>onToggleSelection?.(item.id)}/></label>}<button className="media-hit" onClick={() => selectionMode ? onToggleSelection?.(item.id) : onOpen(item)} aria-label={`Open ${item.name}`}><div className={`media-preview kind-${item.kind}`} style={{ background: url ? undefined : item.accent }}>{url ? <img src={url} alt="" decoding="async" /> : <span className="illustration">{illustration}</span>}<span className="file-pill">{item.extension}</span>{item.duration && <span className="duration-pill">{item.duration}</span>}{!url && item.processing === 'processing' && <span className="work-pill">Preparing…</span>}{!url && item.processing === 'failed' && <span className="error-pill" title={item.error}>Preview unavailable</span>}</div></button><div className="media-details"><strong title={item.relativePath}>{item.name}</strong><span>{item.date} · {item.size}</span><button className={`favorite-button ${item.favorite ? 'set' : ''}`} onClick={() => void onFavorite(item)} aria-label={item.favorite ? `Remove ${item.name} from favorites` : `Favorite ${item.name}`} aria-pressed={!!item.favorite}>{item.favorite ? '♥' : '♡'}</button></div></article>
})

export default App
