import HeicWorker from './heic.worker?worker&inline'

let worker: Worker | undefined
/** The priority queue serializes requests; interruption releases decoder memory. */
export function decodeHeic(blob: Blob, full: boolean, signal?: AbortSignal): Promise<Blob> {
  if (signal?.aborted) return Promise.reject(new DOMException('Conversion paused', 'AbortError'))
  return new Promise((resolve, reject) => {
    const instance = worker ??= new HeicWorker()
    const cleanup = () => { clearTimeout(timeout); instance.removeEventListener('message', message); instance.removeEventListener('error', error); signal?.removeEventListener('abort', cancelled) }
    const terminate = () => { instance.terminate(); if (worker === instance) worker = undefined }
    const message = (event: MessageEvent<{ blob?: Blob; error?: string }>) => { cleanup(); if (event.data.blob) resolve(event.data.blob); else reject(new Error(event.data.error || 'HEIC conversion failed')) }
    const error = (event: ErrorEvent) => { cleanup(); terminate(); reject(new Error(event.message || 'HEIC worker could not decode this file')) }
    const cancelled = () => { cleanup(); terminate(); reject(new DOMException('Conversion paused', 'AbortError')) }
    const timeout = setTimeout(() => { cleanup(); terminate(); reject(new Error('HEIC conversion timed out. Try opening this file again.')) }, 45000)
    instance.addEventListener('message', message); instance.addEventListener('error', error); signal?.addEventListener('abort', cancelled, { once: true })
    instance.postMessage({ blob, full })
  })
}
