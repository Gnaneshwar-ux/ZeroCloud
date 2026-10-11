export type Person = { id: string; name: string; count: number; faceId: string; mediaIds: string[]; aliases?:string[]; isMe?:boolean }
export type EngineStatus = { state: string; current: string; done: number; total: number; failed: number; error?: string; enabled: boolean; paused: boolean; allPaused?: boolean; cacheError?:string; scan?:{state:string;done:number;current:string;error?:string}; locations?: {state:string;current:string;done:number;total:number;tagged:number;failed:number} }
export type StorageStatus = { path: string; cacheBytes: number; databaseBytes: number; cacheLimit: number; originalCopies: number }
export interface DesktopBridge { request<T = unknown>(op: string, args?: Record<string, unknown>): Promise<T> }
declare global { interface Window { velaDesktop?: DesktopBridge } }
const native = window.velaDesktop
export const desktop: DesktopBridge | undefined = native ? {
  request<T = unknown>(op: string, args?: Record<string, unknown>): Promise<T> {
    return native.request<T>(op,args).catch((error) => {
      const message=error instanceof Error?error.message:String(error)
      throw new Error(message.replace(/^Error invoking remote method 'vela:request':\s*(?:Error:\s*)?/,''))
    })
  },
} : undefined
export function mediaUrl(id: string) { return `vela://original/${encodeURIComponent(id)}` }
export function faceUrl(id: string) { return `vela://face/${encodeURIComponent(id)}` }
