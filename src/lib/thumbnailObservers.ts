type Listener = (near:boolean,visible:boolean)=>void
const listeners=new Map<Element,{listener:Listener;near:boolean;visible:boolean}>()
let nearby:IntersectionObserver|undefined,onscreen:IntersectionObserver|undefined
export function observeThumbnail(element:Element,listener:Listener) {
 if(!nearby)nearby=new IntersectionObserver(entries=>{
  for(const entry of entries){const state=listeners.get(entry.target);if(state){state.near=entry.isIntersecting;state.listener(state.near,state.visible)}}
 },{rootMargin:'450px'})
 if(!onscreen)onscreen=new IntersectionObserver(entries=>{
  for(const entry of entries){const state=listeners.get(entry.target);if(state){state.visible=entry.isIntersecting;state.listener(state.near,state.visible)}}
 })
 listeners.set(element,{listener,near:false,visible:false});nearby.observe(element);onscreen.observe(element)
 return()=>{nearby!.unobserve(element);onscreen!.unobserve(element);listeners.delete(element)}
}
