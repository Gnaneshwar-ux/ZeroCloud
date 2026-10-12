import { chromium } from 'playwright'
import { readFile, mkdir } from 'node:fs/promises'
import { resolve, extname } from 'node:path'
const root=resolve('android/app/src/main/assets'),output=resolve('release/android-ui-qa')
await mkdir(output,{recursive:true})
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'chrome'}:{})})
let checks=0
try {
for(const width of [320,390,768])for(const theme of ['light','dark']){
 const context=await browser.newContext({viewport:{width,height:844},colorScheme:theme})
 const page=await context.newPage(),errors=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('https://appassets.androidplatform.net/**',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname.startsWith('/native/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect fill="#447bbb" width="600" height="800"/><circle fill="#91c89a" cx="300" cy="400" r="150"/></svg>'})
  const path=resolve(root,url.pathname.replace('/assets/',''));if(!path.startsWith(root))return route.abort();
  try{await route.fulfill({contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json'})[extname(path)]||'text/plain',body:await readFile(path)})}catch{await route.fulfill({status:404,body:'Missing fixture'})}
 })
 await page.addInitScript(()=>{
  const media=Array.from({length:36},(_,i)=>({id:'item-'+i,name:'Synthetic photo '+i+'.png',kind:'image',mime:'image/png',size:123456,date:Date.UTC(2025,1,1+Math.floor(i/8)),favorite:i===0,folder:'folder-1'}))
  const folders=[{id:'folder-1',name:'Synthetic SD card',uri:'content://synthetic',count:36,online:true,cache:'',scanning:false}]
  const people=[{id:'person-1',name:'Sample person',face:'face-1',count:12,me:true},{id:'person-2',name:'',face:'face-2',count:8,me:false}]
  window.ZeroCloudNative={request(raw){const r=JSON.parse(raw),a=r.args||{};let result=true;
   switch(r.op){
    case 'load':{let data=media.filter(m=>(!a.kind||m.kind===a.kind)&&(!a.favorite||m.favorite)&&(!a.query||m.name.toLowerCase().includes(a.query.toLowerCase()))&&(!a.from||m.date>=a.from)&&(!a.to||m.date<=a.to));result={items:data.slice(a.offset||0,(a.offset||0)+(a.limit||120)),total:data.length,folders};break}
    case 'status':result={state:'Up to date',done:36,total:36,people:2,gps:4,failed:0,reviews:0,paused:false,jobs:[]};break
    case 'people':result=people;break
    case 'appearances':result=media.slice(0,6).map((m,i)=>({id:'face-'+i,media:m.id,name:m.name}));break
    case 'reviews':result=[{a:'person-1',b:'person-2',firstFace:'face-1',secondFace:'face-2',firstName:'Sample person',secondName:'',distance:.34}];break
    case 'locations':result=media.slice(0,4).map(m=>({id:m.id,lat:17.38,lon:78.48}));break
    case 'memoryOptions':result=[{id:'folder:folder-1',title:'Sample trip',count:36,category:'trips'}];break
    case 'memories':result=[];break
    case 'backups':result=[];break
    case 'storage':result={cacheBytes:123456,faceBytes:5678,reelBytes:0,budgetMB:256,portable:'',backup:'',originalCopies:0};break
   }
   queueMicrotask(()=>window.nativeReply({id:r.id,result}));
  }};
 })
 await page.goto('https://appassets.androidplatform.net/assets/index.html');await page.waitForSelector('.tile')
 for(const view of ['library','recent','favorites','folders','disks','people','places','memories','backup','settings']){
  await page.evaluate(view=>document.querySelector(`#rail [data-view="${view}"]`).click(),view)
  await page.waitForFunction(()=>document.querySelector('#page')&&!document.querySelector('#page').textContent.includes('Loading your local catalog'))
  await page.waitForTimeout(100)
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)
  if(overflow)throw new Error(`Horizontal overflow: ${view} ${width} ${theme}`)
  checks++
  if(['library','people','memories','settings'].includes(view))await page.screenshot({path:resolve(output,`${width}-${theme}-${view}.png`),fullPage:true})
 }
 await page.evaluate(()=>document.querySelector('#rail [data-view="library"]').click());await page.waitForSelector('.tile')
 if(await page.locator('#date-options').isVisible())throw new Error('Date controls shown by default')
 await page.locator('[data-action=dates]').click();if(!await page.locator('#date-options').isVisible())throw new Error('Date toggle failed')
 await page.locator('.tile').first().click();await page.waitForSelector('#viewer:not([hidden]) img')
 await page.locator('[data-action=next]').click();if(await page.locator('#viewer').getAttribute('hidden')!==null)throw new Error('Viewer closes on navigation')
 await page.locator('[data-action=viewerFavorite]').click();await page.locator('[data-action=viewerClose]').click()
 await page.evaluate(()=>document.querySelector('#rail [data-view="people"]').click());await page.waitForSelector('.person')
 await page.locator('[data-action=review]').click();await page.waitForSelector('.pair');await page.locator('[data-action=reviewYes]').click();await page.locator('[data-action=closeDialog]').click()
 if(errors.length)throw new Error(errors.join('\n'))
 await context.close()
}
console.log(`PASS: ${checks} tab/viewport/theme alignment checks, date visibility, persistent viewer navigation and face review UI. Native bridge is mocked; these are not device tests.`)
}finally{await browser.close()}
