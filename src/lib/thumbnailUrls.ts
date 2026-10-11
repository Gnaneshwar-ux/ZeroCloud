import type { PreviewRecord } from './libraryStore'

// Keep recently decoded URLs across virtual-row unmounts, never original images.
const entries = new Map<string, {url:string;bytes:number;users:number}>()
const pending = new Map<string, Promise<string>>()
const MAX_BYTES = 24 * 1024 * 1024
const MAX_ITEMS = 160
function trim() {
 let bytes=Array.from(entries.values()).reduce((n,e)=>n+e.bytes,0)
 for(const [key,entry] of entries) {
  if(entries.size<=MAX_ITEMS&&bytes<=MAX_BYTES)break
  if(entry.users)continue
  URL.revokeObjectURL(entry.url);entries.delete(key);bytes-=entry.bytes
 }
}
export function cachedThumbnailUrl(key:string) { return entries.get(key)?.url ?? '' }
export async function retainThumbnailUrl(key:string,load:()=>Promise<PreviewRecord>) {
 let entry=entries.get(key)
 if(!entry) {
  let job=pending.get(key)
  if(!job) {
   job=load().then(preview=>{
    const url=URL.createObjectURL(preview.blob)
    entries.set(key,{url,bytes:preview.blob.size,users:0})
    return url
   }).finally(()=>pending.delete(key))
   pending.set(key,job)
  }
  await job;entry=entries.get(key)!
 }
 entry.users++;entries.delete(key);entries.set(key,entry);trim()
 return entry.url
}
export function releaseThumbnailUrl(key:string) {
 const entry=entries.get(key);if(entry)entry.users=Math.max(0,entry.users-1)
 trim()
}
