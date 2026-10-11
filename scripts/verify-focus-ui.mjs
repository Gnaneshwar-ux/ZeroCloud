import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
const root=process.argv[2],work=process.argv[3],outputs=process.argv[4]
const require=createRequire(path.join(root,'package.json')),{_electron:electron}=require('playwright')
await fs.mkdir(work,{recursive:true});await fs.mkdir(outputs,{recursive:true})
const originals=path.join(work,'originals');await fs.mkdir(originals,{recursive:true})
const app=await electron.launch({executablePath:process.env.VELA_EXECUTABLE||path.join(root,'node_modules/electron/dist/electron.exe'),args:process.env.VELA_PACKAGED==='1'?[]:[path.join(root,'desktop/main.cjs')],env:{...process.env,VELA_TEST:'1',VELA_DATA:path.join(work,'catalog'),VELA_USER_DATA:path.join(work,'profile'),VELA_TEST_PICK:originals},timeout:60000})
const errors=[]
try {
  const page=await app.firstWindow();await page.getByRole('button',{name:'Add folder',exact:true}).first().waitFor();await page.waitForLoadState('load')
  await page.addInitScript(()=>Object.defineProperty(document,'hidden',{get:()=>false}));await page.reload();await page.setViewportSize({width:1440,height:900})
  page.on('pageerror',error=>errors.push(error.message))
  const api=(op,args={})=>page.evaluate(({op,args})=>window.velaDesktop.request(op,args),{op,args})
  const colors=['#b9cedc','#d9bd9a','#82a8a0','#c1acc8','#b7bd87','#91b5d5','#d29d89','#a4b8d2']
  for(let index=0;index<colors.length;index++) {
    const bytes=await page.evaluate(async({color,index})=>{const c=document.createElement('canvas');c.width=800;c.height=600;const x=c.getContext('2d');x.fillStyle=color;x.fillRect(0,0,800,600);x.fillStyle='#ffffff38';x.beginPath();x.arc(590,120,150,0,Math.PI*2);x.fill();x.fillStyle='#26384a50';x.beginPath();x.moveTo(0,600);x.lineTo(240,240);x.lineTo(560,600);x.fill();x.font='32px sans-serif';x.fillStyle='#fff';x.fillText(`Local photo ${index+1}`,35,555);return [...new Uint8Array(await (await new Promise(r=>c.toBlob(r,'image/jpeg'))).arrayBuffer())]},{color:colors[index],index})
    await fs.writeFile(path.join(originals,`photo-${index+1}.jpg`),Buffer.from(bytes))
  }
  await page.getByRole('button',{name:'Add folder',exact:true}).first().click();await page.getByRole('button',{name:'Open photo-1.jpg',exact:true}).waitFor({timeout:60000})
  await page.waitForFunction(()=>document.querySelectorAll('.media-preview img').length===8,null,{timeout:60000});await page.getByRole('button',{name:/^Indexing activity:/}).click();await page.getByRole('button',{name:'Stop all indexing',exact:true}).click();await page.getByRole('button',{name:'Library',exact:true}).click()
  await page.waitForFunction(()=>document.querySelector('.activity-chip').textContent.includes('stopped'))
  async function capture(name){const size=page.viewportSize();await app.evaluate(({BrowserWindow},size)=>{const w=BrowserWindow.getAllWindows()[0];w.setContentSize(size.width,size.height);w.showInactive()},size);try{await page.waitForTimeout(250);await page.screenshot({path:path.join(outputs,name),timeout:15000})}finally{await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].hide())}}
  async function navigationFits(){const size=page.viewportSize();const bounds=await page.locator('.sidebar').evaluate(e=>({height:e.clientHeight,scroll:e.scrollHeight,buttons:[...e.querySelectorAll('button')].map(b=>({top:b.getBoundingClientRect().top,bottom:b.getBoundingClientRect().bottom}))}));assert.equal(bounds.height,bounds.scroll,JSON.stringify(bounds));assert.ok(bounds.buttons.every(b=>b.top>=0&&b.bottom<=size.height),JSON.stringify(bounds));assert.equal(await page.locator('.sidebar .nav-group').count(),3)}
  await navigationFits();assert.ok((await page.locator('.virtual-gallery').boundingBox()).y<260)
  assert.equal(await page.locator('.stats-grid:visible,.job-panel:visible,.indexing-controls:visible').count(),0)
  await capture('zerocloud-library.png')
  for(const name of ['Recent','Favorites','People','Places','Folders','Disks & folders','Duplicates','Backup & protect']){
    await page.getByRole('button',{name,exact:true}).click();assert.equal(await page.locator('.stats-grid:visible,.job-panel:visible,.indexing-controls:visible').count(),0,`${name} has no global panels`)
  }
  await page.getByRole('button',{name:'Library',exact:true}).click();await page.getByRole('button',{name:'Photos',exact:true}).click();assert.equal(await page.locator('.sidebar .nav-item.active').count(),1)
  await page.getByRole('button',{name:'Collapse navigation',exact:true}).click();await navigationFits();await capture('zerocloud-navigation.png')
  await page.getByRole('button',{name:/^Indexing activity:/}).click();await page.getByRole('tab',{name:'Indexing & activity',exact:true}).waitFor();assert.equal(await page.getByRole('tab',{name:'Indexing & activity'}).getAttribute('aria-selected'),'true')
  await page.getByRole('button',{name:'Resume all indexing',exact:true}).click();assert.equal((await api('status')).allPaused,false)
  await page.getByRole('button',{name:'Stop all indexing',exact:true}).click();assert.equal((await api('status')).allPaused,true)
  await capture('zerocloud-activity.png')
  await page.getByRole('tab',{name:'Storage & workspace',exact:true}).click();await page.getByRole('button',{name:'Save workspace',exact:true}).waitFor();assert.equal(await page.locator('.stats-grid:visible,.indexing-controls:visible').count(),0)
  await page.getByRole('tab',{name:'Storage & workspace',exact:true}).focus();await page.keyboard.press('ArrowRight');assert.equal(await page.getByRole('tab',{name:'Indexing & activity'}).getAttribute('aria-selected'),'true');await page.keyboard.press('Home');assert.equal(await page.getByRole('tab',{name:'Storage & workspace'}).getAttribute('aria-selected'),'true')
  await page.reload();await page.getByRole('button',{name:/^Indexing activity:/}).click();await page.getByRole('button',{name:'Resume all indexing',exact:true}).waitFor()
  await page.getByRole('button',{name:'Library',exact:true}).click()
  for(const size of [{width:1440,height:600},{width:400,height:480}]){await page.setViewportSize(size);await page.waitForTimeout(250);await navigationFits();await page.getByRole('button',{name:'Expand navigation',exact:true}).click();await navigationFits();await page.getByRole('button',{name:'Collapse navigation',exact:true}).click();await navigationFits()}
  assert.ok((await page.locator('.virtual-gallery').boundingBox()).y<260);await capture('zerocloud-mobile.png')
  await page.getByRole('button',{name:'Expand navigation',exact:true}).click();await page.getByRole('button',{name:'People',exact:true}).click();assert.equal(await page.locator('.rail-collapsed').count(),1);assert.equal(await page.evaluate(()=>window.scrollY),0)
  assert.deepEqual(errors,[])
  console.log('PASS content-first on all 9 media/backup routes; Settings-only processing details; active navigation; all sidebar controls fit 1440x900, 1440x600 and 400x480 expanded/collapsed; Stop/Resume persistence; workspace controls; no renderer errors.')
} finally {await app.close()}
