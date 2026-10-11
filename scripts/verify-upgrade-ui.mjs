import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import {createRequire} from 'node:module'
import {execFileSync} from 'node:child_process'
const [root,work,fixtures]=process.argv.slice(2).map((value)=>path.resolve(value))
const require=createRequire(path.join(root,'package.json')), { _electron:electron }=require('playwright')
await fs.mkdir(work,{recursive:true});await fs.cp(path.join(fixtures,'catalog'),path.join(work,'catalog'),{recursive:true});await fs.cp(path.join(fixtures,'originals'),path.join(work,'originals'),{recursive:true})
// Add two distinct synthetic groups to test the correction UI, not recognition accuracy.
execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-c',`import sqlite3,json,shutil;from pathlib import Path;p=Path(${JSON.stringify(work)});db=sqlite3.connect(p/'catalog/catalog.sqlite');folder=db.execute('select id from folders limit 1').fetchone()[0];media=db.execute('select id from media where path="gps.jpg"').fetchone()[0];cache=p/'originals/.vela-cache/faces';cache.mkdir(exist_ok=True);existing=next(cache.glob('*.jpg'));[(db.execute('insert into people values(?,?,?)',(f'ui-person-{i}',f'UI person {i}',json.dumps([0.0]*128))),shutil.copyfile(existing,cache/f'ui-face-{i}.jpg'),db.execute('insert into faces values(?,?,?,?,?,?)',(f'ui-face-{i}',media,f'ui-person-{i}',json.dumps([0.0]*128),json.dumps({'x':i*30,'y':0,'w':20,'h':20}),f'@{folder}/faces/ui-face-{i}.jpg'))) for i in range(2)];db.commit();db.close()`],{windowsHide:true})
let application
try {
  application=await electron.launch({executablePath:process.env.VELA_EXECUTABLE||path.join(root,'node_modules/electron/dist/electron.exe'),args:process.env.VELA_PACKAGED==='1'?[]:[path.join(root,'desktop/main.cjs')],env:{...process.env,VELA_TEST:'1',VELA_DATA:path.join(work,'catalog'),VELA_USER_DATA:path.join(work,'profile')},timeout:60000})
  const page=await application.firstWindow(); await page.getByRole('textbox',{name:'Search media'}).waitFor();await page.evaluate(()=>window.velaDesktop.request('indexControl',{paused:false}));await page.addInitScript(()=>Object.defineProperty(document,'hidden',{get:()=>false}));await page.reload();await page.setViewportSize({width:1440,height:1000})
  const errors=[];page.on('pageerror',(error)=>errors.push(error.message))
  const search=page.getByRole('textbox',{name:'Search media'})
  const snapshot=async(name)=>{const size=page.viewportSize();await application.evaluate(({BrowserWindow},size)=>{const w=BrowserWindow.getAllWindows()[0];w.setContentSize(size.width,size.height);w.showInactive()},size);try{await page.waitForTimeout(250);await page.screenshot({path:path.join(work,name),timeout:15000})}finally{await application.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].hide())}}
  await search.fill('misnamed.heic');await page.getByRole('button',{name:'Open misnamed.heic',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.full-image')?.naturalHeight===4000)
  assert.equal(await page.locator('.full-image').evaluate((image)=>image.naturalWidth),2252)
  await page.getByRole('button',{name:'Close viewer',exact:true}).click();await search.fill('zero.jpg');await page.getByRole('button',{name:'Open zero.jpg',exact:true}).click();await page.getByRole('alert').filter({hasText:'only zero bytes'}).waitFor();await page.getByRole('button',{name:'Close viewer',exact:true}).click()
  console.log('PASS native UI: misnamed HEIC/JPEG opens at 2252x4000; zero-filled JPG gives actionable corruption diagnosis')
  await search.fill('sample.jpg');await page.getByRole('button',{name:'Open sample.jpg',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.full-image')?.naturalWidth===1400)
  const percentage=async()=>Number((await page.getByLabel('Zoom percentage').innerText()).replace('%',''))
  let previous=await percentage(); const stage=page.locator('.viewer-stage')
  await stage.dblclick({position:{x:600,y:250}});await page.waitForFunction((percent)=>Number(document.querySelector('.zoom-controls output').textContent.replace('%',''))===percent,previous+5);assert.equal(await percentage(),previous+5)
  previous=await percentage();await stage.dblclick({position:{x:850,y:350}});await page.waitForFunction((percent)=>Number(document.querySelector('.zoom-controls output').textContent.replace('%',''))===percent,previous+5)
  // Zoom to an oversized image, then check click anchoring with no edge clamp.
  await page.getByRole('button',{name:'100% view',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.zoom-controls output').textContent==='100%')
  await stage.evaluate((element)=>{const rect=element.getBoundingClientRect();for(let i=0;i<4;i++)element.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-150,clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2}))})
  await page.waitForFunction(()=>parseInt(document.querySelector('.zoom-controls output').textContent)>300)
  const anchor=await stage.evaluate((element)=>{const stage=element.getBoundingClientRect(),image=document.querySelector('.full-image').getBoundingClientRect(),x=stage.left+stage.width*.6,y=stage.top+stage.height*.6;return {x,y,u:(x-image.left)/image.width,v:(y-image.top)/image.height,percent:parseInt(document.querySelector('.zoom-controls output').textContent)}})
  await stage.evaluate((element,point)=>element.dispatchEvent(new MouseEvent('dblclick',{bubbles:true,clientX:point.x,clientY:point.y})),anchor)
  await page.waitForFunction((percent)=>parseInt(document.querySelector('.zoom-controls output').textContent)===percent,anchor.percent+5)
  const after=await page.locator('.full-image').evaluate((element,point)=>{const rect=element.getBoundingClientRect();return {u:(point.x-rect.left)/rect.width,v:(point.y-rect.top)/rect.height}},anchor)
  assert.ok(Math.abs(after.u-anchor.u)<.001&&Math.abs(after.v-anchor.v)<.001,JSON.stringify({anchor,after}))
  await page.mouse.move(500,500);await page.mouse.down()
  const drag=await stage.evaluate((element)=>{const image=document.querySelector('.full-image'),before=image.getBoundingClientRect();for(let i=1;i<=30;i++)element.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerId:1,buttons:1,clientX:500+i*4,clientY:500+i*2}));return {x:before.left,y:before.top}})
  await page.mouse.up()
  await page.waitForFunction((before)=>Math.abs(document.querySelector('.full-image').getBoundingClientRect().left-before.x-120)<1,drag)
  const dragged=await page.locator('.full-image').boundingBox();assert.ok(Math.abs(dragged.y-drag.y-60)<1)
  await page.getByRole('button',{name:'Fit to screen',exact:true}).click();await page.waitForFunction(()=>{const image=document.querySelector('.full-image').getBoundingClientRect(),stage=document.querySelector('.viewer-stage').getBoundingClientRect();return image.left>=stage.left-1&&image.right<=stage.right+1&&image.top>=stage.top-1&&image.bottom<=stage.bottom+1})
  await page.getByRole('button',{name:'Close viewer',exact:true}).click();console.log('PASS viewer: repeated double-click +5 percentage points, clicked-point anchoring, 30 queued pointer moves accumulate exactly 120x60px, whole-image fit')
  await page.getByRole('button',{name:'People',exact:true}).click();await page.getByRole('button',{name:'Show photos of Alice',exact:true}).waitFor();const cardCount=await page.locator('.person-card').count()
  await page.getByRole('button',{name:'Show photos of Alice',exact:true}).click();await page.getByRole('button',{name:'Back to People',exact:false}).click();await page.getByRole('button',{name:'Show photos of Alice',exact:true}).waitFor();assert.equal(await page.locator('.person-card').count(),cardCount)
  await page.getByRole('button',{name:'Show photos of Alice',exact:true}).click();await page.goBack();await page.getByRole('button',{name:'Show photos of Alice',exact:true}).waitFor()
  await page.getByRole('checkbox',{name:'Select Alice',exact:true}).check();await page.getByRole('checkbox',{name:'Select UI person 0',exact:true}).check();await page.getByRole('button',{name:'Merge selected',exact:true}).click();await page.getByRole('button',{name:'Confirm merge',exact:true}).click();await page.getByRole('status').filter({hasText:'Groups merged'}).waitFor();assert.equal(await page.locator('.person-card').count(),cardCount-1)
  await page.getByRole('checkbox',{name:'Select UI person 1',exact:true}).check();await page.getByRole('button',{name:'Not a face',exact:true}).click();await page.getByRole('button',{name:'Confirm not a face',exact:true}).click();await page.getByRole('status').filter({hasText:'False faces removed'}).waitFor();assert.equal(await page.locator('.person-card').count(),cardCount-2)
  console.log('PASS People: explicit + browser Back return to previous groups, bulk confirmed merge, Not a face correction')
  await page.getByRole('button',{name:/^Indexing activity:/}).click();await page.getByRole('button',{name:'Stop all indexing',exact:true}).click();assert.equal((await page.evaluate(()=>window.velaDesktop.request('status'))).allPaused,true);await page.reload();await page.getByRole('button',{name:/^Indexing activity:/}).click();await page.getByRole('button',{name:'Resume all indexing',exact:true}).waitFor();await page.getByRole('button',{name:'Resume all indexing',exact:true}).click();assert.equal((await page.evaluate(()=>window.velaDesktop.request('status'))).allPaused,false)
  await page.getByRole('button',{name:'Places',exact:true}).click();await page.locator('.offline-map path[fill="#286dc4"]').first().waitFor();await page.getByRole('button',{name:'1 photo',exact:false}).click();await page.getByRole('button',{name:'Back to Places',exact:false}).click();await page.locator('.offline-map').waitFor();await snapshot('places.png')
  await page.getByRole('button',{name:'Collapse navigation',exact:true}).click();await snapshot('sidebar.png')
  assert.ok((await page.title()).includes('ZeroCloud'))
  const disk=path.join(work,'new-disk');await fs.mkdir(disk);await fs.copyFile(path.join(work,'originals/sample.jpg'),path.join(disk,'new-photo.jpg'))
  await page.getByRole('button',{name:'Disks & folders',exact:true}).click();await page.getByRole('textbox',{name:'Disk or folder path',exact:true}).fill(disk);await page.getByRole('button',{name:'Open path',exact:true}).click();await page.getByRole('button',{name:'Index this folder',exact:true}).click();await page.getByRole('textbox',{name:'Search media',exact:true}).fill('new-photo.jpg');await page.getByRole('button',{name:'Open new-photo.jpg',exact:true}).waitFor({timeout:30000});await page.getByRole('button',{name:'Open new-photo.jpg',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.full-image')?.naturalWidth===1400);await page.getByRole('button',{name:'Close viewer',exact:true}).click()
  console.log('PASS ZeroCloud branding, disk explorer path browsing, asynchronous folder indexing and original full-quality opening')
  assert.deepEqual(errors,[])
  console.log('PASS global stop/resume survives refresh; offline Leaflet country map + real GPS marker filtering + Back; no renderer exceptions')
} finally {await application?.close()}
