import { useEffect } from 'react'

/** Ease discrete mouse-wheel notches; leave touchpads, pinch and nested viewers native. */
export function useSmoothGalleryScroll() {
 useEffect(()=>{
  const reduced=matchMedia('(prefers-reduced-motion: reduce)')
  let frame=0,target=0,last=0,touchpadUntil=0
  const cancel=()=>{if(frame)cancelAnimationFrame(frame);frame=0;last=0}
  const tick=(now:number)=>{
   const dt=last?Math.min(40,now-last):16;last=now
   target=Math.max(0,Math.min(document.documentElement.scrollHeight-innerHeight,target))
   const difference=target-scrollY
   const next=Math.abs(difference)<.75?target:scrollY+difference*(1-Math.exp(-dt/65))
   window.scrollTo({top:next,behavior:'instant'})
   if(Math.abs(target-scrollY)>.75)frame=requestAnimationFrame(tick);else {frame=0;last=0}
  }
  const wheel=(event:WheelEvent)=>{
   if(reduced.matches||event.ctrlKey||event.metaKey||Math.abs(event.deltaX)>Math.abs(event.deltaY)||!event.deltaY)return
   if(document.querySelector('dialog[open]')||!document.querySelector('.virtual-gallery'))return
   let element=event.target instanceof Element?event.target:null
   while(element&&element!==document.body){
    if(element.matches('.sidebar,input,textarea,select,[contenteditable=true]'))return
    const style=getComputedStyle(element)
    if(/auto|scroll/.test(style.overflowY)&&element.scrollHeight>element.clientHeight+1)return
    element=element.parentElement
   }
   // Small pixel events are typically touchpad momentum: do not add artificial inertia.
   if(event.deltaMode===0&&(Math.abs(event.deltaY)<80||!Number.isInteger(event.deltaY))){touchpadUntil=performance.now()+500;cancel();return}
   if(event.deltaMode===0&&performance.now()<touchpadUntil){cancel();return}
   const delta=event.deltaY*(event.deltaMode===1?32:event.deltaMode===2?innerHeight:.85)
   const maximum=document.documentElement.scrollHeight-innerHeight
   if((scrollY<=0&&delta<0)||(scrollY>=maximum-1&&delta>0))return
   event.preventDefault()
   if(!frame)target=scrollY
   target=Math.max(0,Math.min(maximum,target+delta))
   if(!frame)frame=requestAnimationFrame(tick)
  }
  window.addEventListener('wheel',wheel,{passive:false})
  window.addEventListener('pointerdown',cancel,{passive:true});window.addEventListener('keydown',cancel);window.addEventListener('touchstart',cancel,{passive:true})
  reduced.addEventListener('change',cancel)
  return()=>{cancel();window.removeEventListener('wheel',wheel);window.removeEventListener('pointerdown',cancel);window.removeEventListener('keydown',cancel);window.removeEventListener('touchstart',cancel);reduced.removeEventListener('change',cancel)}
 },[])
}
