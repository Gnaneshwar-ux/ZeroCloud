import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent } from 'react'
import { initialOriginalUrl, pinOriginal, recoverImage, type RuntimeMedia } from '../lib/runtimeMedia'
import { getPreview } from '../lib/libraryStore'
import { cachedThumbnailUrl,retainThumbnailUrl,releaseThumbnailUrl } from '../lib/thumbnailUrls'
import { Icon } from './Icon'
import { useViewerKeys } from '../lib/useViewerKeys'

type ViewerProps = { media: RuntimeMedia; open: (item: RuntimeMedia) => Promise<string>; onReady?:(id:string)=>void; onClose: () => void; onFavorite: () => void; onNavigate: (direction: -1 | 1, videosOnly?: boolean) => void; hasPrevious: boolean; hasNext: boolean; position: string; allowFavorite?:boolean }

export function Viewer(props: ViewerProps) {
  // Fullscreen belongs to the stable shell, not the keyed decoder/player.
  // Replacing a fullscreen DOM node exits fullscreen in Chromium.
  const shell = useRef<HTMLDivElement>(null)
  return <div ref={shell} className="viewer-backdrop immersive-backdrop" role="dialog" aria-modal="true" aria-label={props.media.name}><ViewerContent key={`${props.media.id}@${props.media.version}`} {...props} onFullscreen={() => { if (document.fullscreenElement) void document.exitFullscreen(); else void shell.current?.requestFullscreen?.().catch(() => {}) }} /></div>
}

function ViewerContent({ media, open, onReady, onClose, onFavorite, onNavigate, hasPrevious, hasNext, position, allowFavorite = true, onFullscreen }: ViewerProps & { onFullscreen: () => void }) {
  const isImage = media.kind === 'image' || media.kind === 'raw'
  const isVisual = isImage || media.kind === 'video'
  const [url, setUrl] = useState(()=>initialOriginalUrl(media))
  const previewKey=`${media.id}@${media.version}`
  const [preview,setPreview]=useState(()=>cachedThumbnailUrl(previewKey))
  const [error, setError] = useState('')
  const [status, setStatus] = useState('Preparing original…')
  const [natural, setNatural] = useState({ width: 0, height: 0 })
  const [size, setSize] = useState({ width: 1, height: 1 })
  const [zoom, setZoom] = useState(1)
  const [controlsVisible, setControlsVisible] = useState(true)
  const [infoOpen, setInfoOpen] = useState(false)
  const hideControls = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const revealControls = useCallback(() => {
    setControlsVisible(true)
    clearTimeout(hideControls.current)
    hideControls.current = setTimeout(() => {
      if (dialog.current?.contains(document.activeElement)) stage.current?.focus({ preventScroll: true })
      setControlsVisible(false)
    }, 4000)
  }, [])
  const imageRef = useRef<HTMLImageElement>(null)
  const transform = useRef({ zoom: 1, x: 0, y: 0 })
  const geometry = useRef({ width: 0, height: 0, fit: 1, stageWidth: 1, stageHeight: 1 })
  const frame = useRef(0)
  const paintTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const recovering = useRef(false)
  const alive = useRef(true)
  const stage = useRef<HTMLDivElement>(null)
  const dialog = useRef<HTMLElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  const video = useRef<HTMLVideoElement>(null)
  const shuttle = useViewerKeys({ kind: media.kind, video, onNavigate })
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef({ x: 0, y: 0, moved: false })
  const lastClick = useRef({ time: 0, x: 0, y: 0 })
  const fit = natural.width ? Math.min(1, size.width / natural.width, size.height / natural.height) : 1
  const scale = fit * zoom
  useEffect(() => {
    if (infoOpen) { clearTimeout(hideControls.current); setControlsVisible(true) }
    else revealControls()
    return () => clearTimeout(hideControls.current)
  }, [infoOpen, revealControls])
  const constrain = useCallback((value: { x: number; y: number }, nextZoom: number) => {
    const g = geometry.current
    const x = Math.max(0, (g.width * g.fit * nextZoom - g.stageWidth) / 2)
    const y = Math.max(0, (g.height * g.fit * nextZoom - g.stageHeight) / 2)
    return { x: Math.max(-x, Math.min(x, value.x)), y: Math.max(-y, Math.min(y, value.y)) }
  }, [])
  // Input events accumulate synchronously in a ref. Paint only once per display
  // frame: no stale React-state deltas, layout resizing, or image re-decoding.
  const paint = useCallback(() => {
    if (frame.current) return
    const flush = () => {
      if (!frame.current) return
      cancelAnimationFrame(frame.current)
      frame.current = 0
      clearTimeout(paintTimer.current); paintTimer.current = undefined
      const value = transform.current
      if (imageRef.current) imageRef.current.style.transform = `translate(-50%,-50%) translate3d(${value.x}px,${value.y}px,0) scale(${value.zoom})`
      if (video.current) video.current.style.transform = `translate3d(${value.x}px,${value.y}px,0) scale(${value.zoom})`
      setZoom(value.zoom)
    }
    frame.current = requestAnimationFrame(flush)
    // Hidden/embedded windows can suspend frame callbacks. Keep controls and
    // geometry consistent there without creating a permanent animation loop.
    paintTimer.current = setTimeout(flush, 50)
  }, [])
  const changeZoom = useCallback((value: number, point = { x: 0, y: 0 }) => {
    const current = transform.current
    const next = Math.max(1, Math.min(Math.max(32, 8 / geometry.current.fit), value))
    const ratio = next / current.zoom
    const pan = constrain({ x: point.x - (point.x - current.x) * ratio, y: point.y - (point.y - current.y) * ratio }, next)
    transform.current = { zoom: next, ...pan }; paint()
  }, [constrain, paint])
  function reset() {
    transform.current = { zoom: 1, x: 0, y: 0 }
    if (imageRef.current) imageRef.current.style.transform = 'translate(-50%,-50%) translate3d(0,0,0) scale(1)'
    if (video.current) video.current.style.transform = 'translate3d(0,0,0) scale(1)'
    setZoom(1); paint()
  }
  function pointAt(clientX: number, clientY: number) {
    const rect = stage.current!.getBoundingClientRect()
    return { x: clientX - rect.left - rect.width / 2, y: clientY - rect.top - rect.height / 2 }
  }
  async function imageFailed() {
    if (recovering.current) { setError('The image could not be decoded. Restore a healthy original from your backup.'); return }
    recovering.current = true; setStatus('Checking original image…')
    try { const value = await recoverImage(media); if (alive.current) setUrl(value) }
    catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  useLayoutEffect(() => {
    geometry.current = { width: natural.width, height: natural.height, fit, stageWidth: size.width, stageHeight: size.height }
    const value = transform.current
    transform.current = { zoom: value.zoom, ...constrain(value, value.zoom) }; paint()
  }, [natural, size, fit, constrain, paint])
  useEffect(() => { alive.current = true; return () => { alive.current = false; if (frame.current) cancelAnimationFrame(frame.current); frame.current=0; clearTimeout(paintTimer.current) } }, [])
  useEffect(()=>{
    if(!isVisual||media.folderId==='memories')return
    let active=true,pinned=false
    void retainThumbnailUrl(previewKey,async()=>{const saved=await getPreview(media,'thumbnail');if(!saved)throw new Error('No saved preview');return saved}).then(value=>{pinned=true;if(active)setPreview(value);else releaseThumbnailUrl(previewKey)}).catch(()=>{})
    return()=>{active=false;if(pinned)releaseThumbnailUrl(previewKey)}
    // Reuse saved derivatives only; this path never generates a thumbnail.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  },[previewKey,isVisual])
  useEffect(() => {
    let active = true
    const release=pinOriginal(media)
    void open(media).then((value) => { if (active) setUrl(value) }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)) })
    return () => { active = false;release() }
    // The component is keyed by immutable source version.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [media.id, media.version, open])
  useEffect(() => { const player = video.current; return () => { player?.pause(); player?.removeAttribute('src'); player?.load() } }, [url])
  useEffect(() => {
    const element = stage.current; if (!element) return
    const measure = () => { const rect=element.getBoundingClientRect();setSize({ width:rect.width,height:rect.height }) }
    const observer = new ResizeObserver(measure)
    observer.observe(element); window.addEventListener('resize', measure); window.visualViewport?.addEventListener('resize', measure); measure()
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); window.visualViewport?.removeEventListener('resize', measure) }
  }, [])
  useEffect(() => {
    const element = stage.current; if (!element || !isVisual) return
    const wheel = (event: WheelEvent) => {
      if (!natural.width) return
      event.preventDefault(); const rect = element.getBoundingClientRect()
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1)
      changeZoom(transform.current.zoom * Math.exp(-Math.max(-150, Math.min(150, delta)) * 0.002), { x: event.clientX - rect.left - rect.width / 2, y: event.clientY - rect.top - rect.height / 2 })
    }
    element.addEventListener('wheel', wheel, { passive: false }); return () => element.removeEventListener('wheel', wheel)
  }, [isVisual, natural.width, changeZoom])
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; close.current?.focus()
    return () => { document.body.style.overflow = overflow; previous?.focus() }
  }, [])
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { if (infoOpen) setInfoOpen(false); else onClose() }
      if (event.key === 'Tab') {
        if (!controlsVisible) { event.preventDefault(); revealControls(); requestAnimationFrame(() => close.current?.focus()); return }
        revealControls()
        const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button, video[controls], audio, a') ?? []).filter((element) => !element.hasAttribute('disabled') && element.getClientRects().length > 0)
        const first = controls[0]; const last = controls.at(-1)
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener('keydown', keydown)
    return () => document.removeEventListener('keydown', keydown)
  // Keep the focus trap stable while the overlay visibility changes.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose, infoOpen, controlsVisible, revealControls])
  function move(event: PointerEvent) {
    const before = pointers.current.get(event.pointerId); if (!before) return
    if (pointers.current.size > 1 || Math.hypot(event.clientX - gesture.current.x, event.clientY - gesture.current.y) > 6) gesture.current.moved = true
    const next = { x: event.clientX, y: event.clientY }
    if (pointers.current.size === 2) {
      const other = [...pointers.current.entries()].find(([id]) => id !== event.pointerId)![1]
      const oldDistance = Math.hypot(before.x - other.x, before.y - other.y)
      const distance = Math.hypot(next.x - other.x, next.y - other.y)
      const oldCenter = pointAt((before.x + other.x) / 2, (before.y + other.y) / 2)
      if (oldDistance > 0) changeZoom(transform.current.zoom * distance / oldDistance, oldCenter)
      const current = transform.current
      transform.current = { zoom: current.zoom, ...constrain({ x: current.x + (next.x - before.x) / 2, y: current.y + (next.y - before.y) / 2 }, current.zoom) }; paint()
    } else if (transform.current.zoom > 1) {
      const current = transform.current
      transform.current = { zoom: current.zoom, ...constrain({ x: current.x + next.x - before.x, y: current.y + next.y - before.y }, current.zoom) }; paint()
    }
    pointers.current.set(event.pointerId, next)
  }
  function nativeVideoControls(clientY: number) {
    // Leave Chromium's bottom playback/seek controls to the player.
    return media.kind === 'video' && controlsVisible && !!video.current && clientY >= video.current.getBoundingClientRect().bottom - 64
  }
  function mediaClick(event: React.MouseEvent<HTMLDivElement>) {
    revealControls()
    if (!isVisual || !natural.width || nativeVideoControls(event.clientY)) { lastClick.current.time = 0; return }
    if (gesture.current.moved) { gesture.current.moved = false; lastClick.current.time = 0; event.preventDefault(); return }
    const now = performance.now(), previous = lastClick.current
    if (previous.time && now - previous.time <= 1500 && Math.hypot(event.clientX - previous.x, event.clientY - previous.y) <= 32) {
      event.preventDefault()
      changeZoom(transform.current.zoom + 0.05 / fit, pointAt(event.clientX, event.clientY))
    }
    lastClick.current = { time: now, x: event.clientX, y: event.clientY }
  }
  const dimensions = natural.width ? `${natural.width} × ${natural.height}` : ''
  return <section className={`viewer immersive-viewer ${controlsVisible ? 'controls-visible' : 'controls-hidden'}`} ref={dialog}>
    <header className="viewer-overlay" aria-hidden={!controlsVisible} inert={!controlsVisible} onPointerDown={revealControls}><div className="viewer-actions"><button aria-label="File information" aria-expanded={infoOpen} aria-controls="viewer-information" onClick={() => setInfoOpen(value => !value)} title="File information"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/></svg></button>{allowFavorite&&<button className={`viewer-favorite${media.favorite ? ' is-favorite' : ''}`} onClick={onFavorite} aria-label="Toggle favorite" aria-pressed={!!media.favorite} title={media.favorite ? 'Remove from favorites' : 'Add to favorites'}><Icon name="favorites" /></button>}<button onClick={onFullscreen} aria-label="Fullscreen"><Icon name="fullscreen" /></button><button ref={close} onClick={onClose} aria-label="Close viewer"><Icon name="close" /></button></div></header>
    <div ref={stage} tabIndex={-1} className={`viewer-stage image-canvas ${zoom > 1 ? 'can-pan' : ''}`} onClick={mediaClick} onPointerDown={(event) => { revealControls(); if (!isVisual || !natural.width || event.button !== 0 || nativeVideoControls(event.clientY)) return; gesture.current = { x: event.clientX, y: event.clientY, moved: pointers.current.size > 0 }; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY }) }} onPointerMove={move} onPointerUp={(event) => pointers.current.delete(event.pointerId)} onPointerCancel={(event) => pointers.current.delete(event.pointerId)} onLostPointerCapture={(event) => pointers.current.delete(event.pointerId)}>
      {preview&&!natural.width&&!error&&isVisual&&<><img className="viewer-quick-preview" src={preview} alt="" draggable={false}/><span className="viewer-quality-pending" role="status">Loading full quality…</span></>}
      {error ? <div className="viewer-loading" role="alert">{error}<small>Your original was not modified. Your catalog and thumbnails are retained.</small></div> : !url ? !preview&&<div className="viewer-loading">Opening media…</div>
: media.kind === 'video' ? <video ref={video} src={url} controls={controlsVisible} autoPlay playsInline preload="metadata" onLoadedMetadata={(event) => { const player = event.currentTarget; setNatural({ width: player.videoWidth, height: player.videoHeight }); setStatus('Original quality');onReady?.(media.id) }} onPlaying={() => setStatus('Playing · Original quality')} onWaiting={() => setStatus('Buffering local video…')} onPause={() => setStatus('Paused · Original quality')} onError={() => setError('This video codec is not supported by the player. The original file is unchanged.')} />
      : media.kind === 'audio' ? <audio src={url} controls autoPlay preload="metadata" />
      : media.kind === 'document' ? <iframe src={url} title={media.name} />
      : <img ref={imageRef} className="full-image" src={url} alt={media.name} draggable={false} decoding="async" style={{ width:'auto', height:'auto', maxWidth:'100%', maxHeight:'100%', opacity: natural.width ? 1 : 0 }} onLoad={(event) => { const image = event.currentTarget; setNatural({ width: image.naturalWidth, height: image.naturalHeight }); setStatus('Full resolution');onReady?.(media.id) }} onError={() => void imageFailed()} />}
    </div>
    <footer className="viewer-overlay" aria-hidden={!controlsVisible} inert={!controlsVisible} onPointerDown={revealControls}><div className="viewer-navigation"><button disabled={!hasPrevious} onClick={() => onNavigate(-1)} aria-label="Previous media" title="Previous item in this filtered view"><span style={{display:'flex',transform:'rotate(180deg)'}}><Icon name="chevron" /></span></button><output aria-label="Position in filtered media">{position}</output><button disabled={!hasNext} onClick={() => onNavigate(1)} aria-label="Next media" title="Next item in this filtered view"><Icon name="chevron" /></button></div>{isVisual && <div className="zoom-controls"><button onClick={() => changeZoom(transform.current.zoom - 0.05 / fit)} disabled={zoom <= 1} aria-label="Zoom out"><Icon name="zoomOut" /></button><output aria-label="Zoom percentage">{Math.round(scale * 100)}%</output><button onClick={() => changeZoom(transform.current.zoom + 0.05 / fit)} aria-label="Zoom in"><Icon name="zoomIn" /></button><button onClick={reset} aria-label="Fit to screen" title="Fit to screen"><Icon name="fit" /></button><button onClick={() => changeZoom(1 / fit)} aria-label="100% view" title="Actual size">1:1</button></div>}</footer>
    {infoOpen && <aside id="viewer-information" className="viewer-information" aria-label="File details"><div><h2>Information</h2><button aria-label="Close information" onClick={() => setInfoOpen(false)}><Icon name="close" /></button></div><strong>{media.name}</strong><dl><dt>Format / size</dt><dd>{media.extension} · {media.size}</dd>{dimensions && <><dt>Resolution</dt><dd>{dimensions}</dd></>}<dt>Captured</dt><dd>{media.capturedAt || 'Unknown capture date'}</dd><dt>Original location</dt><dd>{media.relativePath}</dd><dt>Playback / quality</dt><dd>{error ? 'Viewing unavailable' : shuttle === -1 ? '3× reverse seeking' : shuttle === 1 ? 'Playing at 3×' : status}</dd></dl><p>{isImage ? '← / →: previous / next · Scroll to zoom · Drag to pan · Repeated clicks at the same spot: +5%' : media.kind === 'video' ? 'Scroll / pinch to zoom · Drag to pan · Repeated clicks: +5% · ← / →: ±5s · Hold: 3× · Space: play/pause · Shift + arrows: videos' : 'Original file · No re-encoding'}</p><small>Controls hide after 4 seconds. Tap the media to show them again.</small></aside>}
  </section>
}
