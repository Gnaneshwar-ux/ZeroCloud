export type QueueStatus = { pending: number; current: string; paused: boolean }
type Work = { key: string; label: string; priority: number; order: number; run: (signal: AbortSignal) => Promise<unknown>; resolve: (value: unknown) => void; reject: (error: unknown) => void; promise: Promise<unknown> }

/** One decoder at a time. Reading an original video never enters this queue. */
export class PriorityQueue {
  private pending = new Map<string, Work>()
  private running: Work | undefined
  private controller: AbortController | undefined
  private sequence = 0
  private suspended = false
  private backgroundHidden = false
  private scrolling = false
  private timer: ReturnType<typeof setTimeout> | undefined
  private listeners = new Set<(status: QueueStatus) => void>()
  subscribe(listener: (status: QueueStatus) => void) { this.listeners.add(listener); this.emit(); return () => { this.listeners.delete(listener) } }

  enqueue<T>(key: string, label: string, priority: number, run: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const existing = this.pending.get(key) ?? (this.running?.key === key ? this.running : undefined)
    if (existing) { existing.priority = Math.max(priority, existing.priority); return existing.promise as Promise<T> }
    let resolve!: (value: unknown) => void
    let reject!: (error: unknown) => void
    const promise = new Promise<unknown>((yes, no) => { resolve = yes; reject = no })
    this.pending.set(key, { key, label, priority, order: this.sequence++, run, resolve, reject, promise })
    this.emit()
    this.schedule()
    return promise as Promise<T>
  }

  reprioritize(key: string, priority: number) {
    const work = this.pending.get(key)
    if (work) work.priority = priority
    this.schedule()
  }
  cancelPending(key: string, maximumPriority: number) {
    const work=this.pending.get(key)
    if(work && work.priority<=maximumPriority){this.pending.delete(key);work.reject(new DOMException('Prefetch no longer needed','AbortError'));this.emit()}
  }

  pauseBackground(value: boolean) { this.suspended = value; this.interruptBackground(); this.emit(); this.schedule() }
  pageHidden(value: boolean) { this.backgroundHidden = value; this.interruptBackground(); this.emit(); this.schedule() }
  scrollBusy(value: boolean) { this.scrolling = value; this.schedule() }
  private interruptBackground() { if ((this.suspended || this.backgroundHidden) && this.running && this.running.priority < 1000) this.controller?.abort() }
  private emit() { for (const listener of this.listeners) listener({ pending: this.pending.size, current: this.running?.label ?? '', paused: this.suspended || this.backgroundHidden }) }
  private schedule() {
    if (this.running || this.timer !== undefined) return
    this.timer = setTimeout(() => { this.timer = undefined; void this.next() }, 40)
  }
  private async next() {
    if (this.running) return
    const paused = this.suspended || this.backgroundHidden || this.scrolling
    let candidate: Work | undefined
    for (const item of this.pending.values()) {
      if (paused && item.priority < 1000) continue
      if (!candidate || item.priority > candidate.priority || (item.priority === candidate.priority && item.order < candidate.order)) candidate = item
    }
    if (!candidate) { this.emit(); return }
    this.pending.delete(candidate.key)
    this.running = candidate
    this.controller = new AbortController()
    this.emit()
    try { candidate.resolve(await candidate.run(this.controller.signal)) }
    catch (error) {
      if (this.controller.signal.aborted) this.pending.set(candidate.key, candidate)
      else candidate.reject(error)
    } finally { this.running = undefined; this.controller = undefined; this.emit(); if (this.pending.size) this.schedule() }
  }
}
