import { useEffect, useState, type RefObject } from 'react'

export function useViewerKeys({ kind, video, onNavigate }: { kind: string; video: RefObject<HTMLVideoElement | null>; onNavigate: (direction: -1 | 1, videosOnly?: boolean) => void }) {
  const [shuttle, setShuttle] = useState<-1 | 0 | 1>(0)
  useEffect(() => {
    let held: { key: string; direction: -1 | 1; player: HTMLVideoElement; paused: boolean; rate: number; started: boolean; start: number; time: number } | undefined
    let delay: ReturnType<typeof setTimeout> | undefined
    let reverse: ReturnType<typeof setInterval> | undefined
    const seek = (player: HTMLVideoElement, time: number) => {
      if (!Number.isFinite(player.duration) || player.readyState < 1) return
      player.currentTime = Math.max(0, Math.min(player.duration, time))
    }
    const release = (tap = false, restore = true) => {
      clearTimeout(delay); clearInterval(reverse)
      const previous = held; held = undefined
      if (!previous) return
      if (!previous.started) { if (tap) seek(previous.player, previous.player.currentTime + previous.direction * 5); return }
      previous.player.playbackRate = previous.rate
      if (restore && !previous.paused && !previous.player.ended) void previous.player.play().catch(() => {})
      else previous.player.pause()
      setShuttle(0)
    }
    const keydown = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.altKey || event.metaKey || event.isComposing) return
      const target = event.target as HTMLElement | null
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return
      const arrow = event.key === 'ArrowLeft' || event.key === 'ArrowRight'
      if (kind === 'video' && (event.code === 'Space' || event.key === ' ')) {
        event.preventDefault(); event.stopPropagation()
        if (event.repeat) return
        const player = video.current
        const paused = player?.paused
        release(false, false)
        if (player) { if (paused) void player.play().catch(() => {}); else player.pause() }
        return
      }
      if (!arrow || !['image', 'raw', 'video'].includes(kind)) return
      event.preventDefault(); event.stopPropagation()
      const direction = event.key === 'ArrowLeft' ? -1 : 1
      if (kind !== 'video' || event.shiftKey) {
        if (event.repeat) return
        release(false, false); onNavigate(direction, kind === 'video')
        return
      }
      if (event.repeat || held?.key === event.key) return
      release()
      const player = video.current
      if (!player || player.readyState < 1 || !Number.isFinite(player.duration)) return
      held = { key: event.key, direction, player, paused: player.paused, rate: player.playbackRate, started: false, start: 0, time: 0 }
      // A short press seeks once on release. Holding never adds an initial 5s jump.
      delay = setTimeout(() => {
        if (!held) return
        held.started = true; held.start = performance.now(); held.time = player.currentTime
        setShuttle(direction)
        if (direction === 1) { player.playbackRate = 3; void player.play().catch(() => {}) }
        else {
          // Chromium does not reliably support negative playbackRate. Seek the
          // original timeline at 3x instead; at most one decode is in flight.
          player.pause()
          reverse = setInterval(() => {
            if (!held || player.seeking) return
            const next = held.time - (performance.now() - held.start) / 1000 * 3
            seek(player, next)
            if (next <= 0) clearInterval(reverse)
          }, 100)
        }
      }, 300)
    }
    const keyup = (event: KeyboardEvent) => { if (held?.key === event.key) { event.preventDefault(); event.stopPropagation(); release(true) } }
    const blur = () => release()
    const visibility = () => { if (document.hidden) release() }
    document.addEventListener('keydown', keydown, true); document.addEventListener('keyup', keyup, true)
    window.addEventListener('blur', blur); document.addEventListener('visibilitychange', visibility)
    return () => {
      release(false, false)
      document.removeEventListener('keydown', keydown, true); document.removeEventListener('keyup', keyup, true)
      window.removeEventListener('blur', blur); document.removeEventListener('visibilitychange', visibility)
    }
  }, [kind, video, onNavigate])
  return shuttle
}
