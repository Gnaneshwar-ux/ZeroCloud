import { useEffect, useState } from 'react'
import { desktop, type EngineStatus } from './desktop'

// One lightweight poll drives both the global indicator and Settings details.
export function useEngineStatus(stopped: boolean) {
  const [status, setStatus] = useState<EngineStatus>()
  useEffect(() => {
    if (!desktop) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try { const next = await desktop!.request<EngineStatus>('status'); if (active) setStatus(next) }
      catch { /* Library connection errors are surfaced by useLibrary. */ }
      if (active) timer = setTimeout(() => void poll(), 2500)
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [stopped])
  return status
}
