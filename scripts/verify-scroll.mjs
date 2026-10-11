import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
const root=process.cwd(),work=process.argv[2],require=createRequire(root+'/package.json'),{_electron:electron}=require('playwright')
const app=await electron.launch({executablePath:root+'/release/Vela-win-x64-20261010/ZeroCloud.exe',args:[],env:{...process.env,VELA_TEST:'1',VELA_DATA:work+'/catalog',VELA_USER_DATA:work+'/profile'},timeout:60000})
try{
 const page=await app.firstWindow();page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.getByRole('textbox',{name:'Search media'}).waitFor();await page.waitForLoadState('load')
 await page.addInitScript(()=>{window.createdUrls=0;const original=URL.createObjectURL;URL.createObjectURL=function(blob){window.createdUrls++;return original(blob)}});await page.reload()
 await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].showInactive())
 await page.waitForFunction(()=>document.querySelectorAll('.media-card img').length>10)
 assert.ok(await page.locator('.media-card').count()<150)
 const first=await page.locator('.media-card img').first().getAttribute('src')
 await page.evaluate(()=>window.scrollTo({top:1500,behavior:'instant'}));await page.waitForTimeout(600)
 await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.waitForTimeout(500)
 assert.equal(await page.locator('.media-card img').first().getAttribute('src'),first,'Reuse thumbnail URL when returning to rows')
 await page.evaluate(()=>{window.frames=[];window.lastFrame=performance.now();window.frameLoop=requestAnimationFrame(function tick(now){window.frames.push(now-window.lastFrame);window.lastFrame=now;window.frameLoop=requestAnimationFrame(tick)})})
 await page.mouse.move(900,500)
 await page.mouse.wheel(0,120);await page.waitForTimeout(35)
 const intermediate=await page.evaluate(()=>scrollY);assert.ok(intermediate>0&&intermediate<102,`Expected eased wheel step, got ${intermediate}`)
 for(let i=0;i<12;i++){await page.mouse.wheel(0,120);await page.waitForTimeout(35)}
 await page.waitForTimeout(600)
 const stats=await page.evaluate(()=>{cancelAnimationFrame(window.frameLoop);const frames=window.frames.slice(1).sort((a,b)=>a-b);return{frames:frames.length,p95:frames[Math.floor(frames.length*.95)],max:frames.at(-1),position:scrollY}})
 assert.ok(stats.position>1000);assert.ok(await page.locator('.media-card img').count()>10)
 await page.emulateMedia({reducedMotion:'reduce'});await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.mouse.wheel(0,120);await page.waitForTimeout(200);assert.ok(await page.evaluate(()=>scrollY)>0)
 assert.deepEqual(errors,[])
 console.log('PASS: 3,000 cached photo/video cards, bounded DOM, thumbnail URL reuse, eased mouse-wheel steps, reduced-motion scrolling and no renderer exceptions. Frame sample:',JSON.stringify(stats))
}finally{await app.close()}
