import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import {createRequire} from 'node:module'
const root=path.resolve(process.argv[2]||process.cwd()),work=path.resolve(process.argv[3]), require=createRequire(path.join(root,'package.json'))
const { _electron: electron }=require('playwright')
const originals=path.join(work,'originals');await fs.mkdir(originals,{recursive:true})
if(!process.env.VELA_HEIC_SAMPLE)throw new Error('Set VELA_HEIC_SAMPLE to a local HEIC fixture.')
await fs.copyFile(process.env.VELA_HEIC_SAMPLE,path.join(originals,'sample.HEIC'))
await fs.copyFile(process.argv[4],path.join(originals,'movie.webm'))
let application
async function launch(data) {
 application=await electron.launch({executablePath:path.join(root,'node_modules/electron/dist/electron.exe'),args:[path.join(root,'desktop/main.cjs')],env:{...process.env,VELA_TEST:'1',VELA_DATA:data,VELA_USER_DATA:path.join(work,'profile-'+Date.now()),VELA_TEST_PICK:originals},timeout:60000})
 const page=await application.firstWindow();await page.getByRole('button',{name:'Add folder',exact:true}).first().waitFor();await page.addInitScript(()=>Object.defineProperty(document,'hidden',{get:()=>false}));await page.reload();return page
}
try {
 let page=await launch(path.join(work,'catalog'))
 await page.getByRole('button',{name:'Add folder',exact:true}).first().click()
 await page.getByRole('button',{name:'Open sample.HEIC',exact:true}).click()
 await page.waitForFunction(()=>document.querySelector('.full-image')?.naturalWidth===3024,null,{timeout:90000})
 assert.equal(await page.locator('.full-image').evaluate((image)=>image.naturalHeight),4032)
 const bounds=await page.locator('.full-image').evaluate((image)=>{const rect=image.getBoundingClientRect(),stage=document.querySelector('.viewer-stage').getBoundingClientRect();return {top:rect.top,bottom:rect.bottom,start:stage.top,end:stage.bottom}})
 assert.ok(bounds.top>=bounds.start-1&&bounds.bottom<=bounds.end+1)
 await page.getByRole('button',{name:'Close viewer',exact:true}).click()
 const repeat=Date.now();await page.getByRole('button',{name:'Open sample.HEIC',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.full-image')?.naturalWidth===3024)
 console.log(`PASS native HEIC: 3024x4032, entire frame fitted, repeat memory-cache open ${Date.now()-repeat}ms`)
 await page.getByRole('button',{name:'Close viewer',exact:true}).click()
 await page.getByRole('button',{name:'Open movie.webm',exact:true}).click()
 await page.waitForFunction(()=>{const player=document.querySelector('.viewer video');return player?.videoWidth===320&&player.currentTime>.25},null,{timeout:45000})
 assert.ok((await page.locator('.viewer video').getAttribute('src')).startsWith('vela://'))
 assert.equal((await page.evaluate(()=>window.velaDesktop.request('status'))).paused,true)
 await page.getByRole('button',{name:'Close viewer',exact:true}).click()
 await page.waitForFunction(()=>document.querySelector('.job-panel small')?.textContent==='2 / 2',null,{timeout:60000}).catch(async(error)=>{console.log(await page.locator('.job-panel').innerText());console.log(await page.locator('.error-pill').evaluateAll((nodes)=>nodes.map((node)=>node.title)));throw error})
 const storage=await page.evaluate(()=>window.velaDesktop.request('storage'));assert.equal(storage.originalCopies,0);assert.ok(storage.cacheBytes<500000)
 const files=await fs.readdir(path.join(work,'catalog'));assert.ok(!files.some((name)=>name.includes('full')))
 console.log('PASS native video: original streamed playback, poster thumbnail, paused face worker; only small thumbnails saved')
 await application.close();application=undefined
 // UI check on a copied real face index; originals stay linked to the user's folder.
 const faceData=path.join(work,'face-library');await fs.cp(process.argv[5],faceData,{recursive:true})
 page=await launch(faceData);await page.getByRole('button',{name:'People',exact:true}).click()
 await page.locator('.person-card').first().waitFor();const count=await page.locator('.person-card').count();assert.ok(count>0)
 await page.locator('.person-name').first().click();await page.getByRole('textbox',{name:'Person name',exact:true}).fill('Named in UI');await page.getByRole('button',{name:'Save name',exact:true}).click();await page.getByRole('button',{name:'Named in UI',exact:true}).waitFor()
 const person=await page.evaluate(async()=>{const people=await window.velaDesktop.request('people');return people.find((person)=>person.name==='Named in UI')})
 await page.getByRole('button',{name:'Show photos of Named in UI',exact:true}).click();await page.locator('.person-filter').waitFor()
 assert.ok((await page.locator('.library-section .section-heading p').innerText()).startsWith(`${person.count.toLocaleString()} items`))
 await page.reload();await page.getByRole('button',{name:'People',exact:true}).click();await page.getByRole('button',{name:'Named in UI',exact:true}).waitFor()
 console.log(`PASS People UI: ${count} real groups, naming via dialog, group photo filtering, name survives refresh`)
} finally {await application?.close()}
