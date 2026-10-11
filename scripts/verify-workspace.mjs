import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import {createRequire} from 'node:module'
const root=path.resolve(process.argv[2]),work=path.resolve(process.argv[3]);await fs.mkdir(work,{recursive:true})
const require=createRequire(path.join(root,'package.json')),{_electron:electron}=require('playwright')
const workspace=require(path.join(root,'desktop/workspace.cjs'))
const originals=path.join(work,'originals'),catalog=path.join(work,'catalog'),cache=path.join(work,'folder-cache'),file=path.join(work,'saved.zerocloud-workspace.json')
await fs.mkdir(originals,{recursive:true});await fs.writeFile(path.join(originals,'photo.jpg'),'fixture original')
let app
async function launch(data,profile,workspaceFile=file){app=await electron.launch({executablePath:process.env.VELA_EXECUTABLE||path.join(root,'node_modules/electron/dist/electron.exe'),args:process.env.VELA_PACKAGED==='1'?[]:[path.join(root,'desktop/main.cjs')],env:{...process.env,VELA_TEST:'1',VELA_DATA:data,VELA_USER_DATA:profile,VELA_TEST_PICK:originals,VELA_TEST_WORKSPACE:workspaceFile},timeout:60000});const page=await app.firstWindow();await page.getByRole('button',{name:'Add folder',exact:true}).first().waitFor();await page.waitForLoadState('load');await page.addInitScript(()=>Object.defineProperty(document,'hidden',{get:()=>false}));await page.reload();return page}
try{
  let page=await launch(catalog,path.join(work,'profile'))
  const api=(op,args={})=>page.evaluate(({op,args})=>window.velaDesktop.request(op,args),{op,args})
  const added=await api('addFolder');assert.ok(added.backgroundScan)
  for(let i=0;i<100;i++){if((await api('scanStatus')).state==='complete')break;await new Promise(resolve=>setTimeout(resolve,50))}
  const library=await api('load'),item=library.items[0];assert.ok(item);await api('indexControl',{paused:true});await api('favorite',{id:item.id,favorite:true})
  await api('previewSave',{id:item.id,version:item.version,data:Buffer.from('small cached fixture').toString('base64')})
  // Point the folder at its own portable cache through the real desktop picker.
  await fs.mkdir(cache,{recursive:true});await app.evaluate((_,directory)=>{process.env.VELA_TEST_PICK=directory},cache)
  await api('chooseFolderCache',{folderId:item.folderId})
  const backupState=await api('backup:overview')
  await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('button',{name:'Save workspace',exact:true}).click();await page.getByRole('status').filter({hasText:'Workspace saved:'}).waitFor()
  const saved=await workspace.read(file);assert.equal(saved.folders.length,1);assert.equal(saved.folders[0].cache.absolute,cache);assert.equal(saved.backupState.absolute,backupState.statePath);assert.ok((await fs.stat(file)).size<10000)
  // Opening from a separate blank library must switch back without a folder scan.
  await app.close();app=undefined
  page=await launch(path.join(work,'blank'),path.join(work,'profile-blank'))
  await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('button',{name:'Open workspace',exact:true}).click()
  await page.getByRole('button',{name:'Open photo.jpg',exact:true}).waitFor({timeout:15000}).catch(async error=>{console.log('Workspace window:',await app.evaluate(({BrowserWindow})=>({url:BrowserWindow.getAllWindows()[0].webContents.getURL(),loading:BrowserWindow.getAllWindows()[0].webContents.isLoading()})));console.log('Catalog after open:',await api('load').catch(String));console.log('Window text:',await page.locator('body').innerText({timeout:3000}).catch(String));throw error})
  let restored;for(let i=0;i<100;i++){restored=await api('load');if(restored.items.length)break;await new Promise(resolve=>setTimeout(resolve,50))}
  assert.equal(restored.items[0].id,item.id);assert.equal(restored.items[0].favorite,true);assert.equal(restored.items[0].thumbnailReady,true);assert.equal(restored.folders[0].cacheLocation,cache);assert.equal((await api('scanStatus')).state,'idle');assert.equal((await api('backup:overview')).statePath,backupState.statePath)
  console.log('PASS Save/Open workspace UI: separate profile loads original catalog, folder cache path, favorite, stable IDs and cached thumbnail without rescanning')
  await app.close();app=undefined
  const moved=path.join(work,'moved');await fs.mkdir(moved,{recursive:true});for(const name of ['catalog','originals','folder-cache'])await fs.cp(path.join(work,name),path.join(moved,name),{recursive:true});await fs.copyFile(file,path.join(moved,path.basename(file)))
  const relocatedFile=path.join(moved,path.basename(file));assert.equal(await workspace.findLibrary(relocatedFile,saved.library.location,saved.library.id),path.join(moved,'catalog'))
  page=await launch(path.join(work,'blank-moved'),path.join(work,'profile-moved'),relocatedFile)
  await api('openWorkspace');restored=await api('load');assert.equal(restored.folders[0].location,path.join(moved,'originals'));assert.equal(restored.folders[0].cacheLocation,path.join(moved,'folder-cache'));assert.equal(restored.items[0].favorite,true);assert.equal(restored.items[0].thumbnailReady,true);assert.equal((await api('scanStatus')).state,'idle')
  const invalid=path.join(work,'invalid.json');await fs.writeFile(invalid,JSON.stringify({format:'zerocloud-workspace',version:99}));await assert.rejects(workspace.read(invalid),/supported/)
  await fs.rename(path.join(moved,'originals'),path.join(moved,'offline-originals'));await fs.rename(originals,path.join(work,'offline-originals'));await api('openWorkspace');const offline=await api('load');assert.equal(offline.items.length,1);assert.equal(offline.folders[0].online,false);assert.equal(offline.items[0].thumbnailReady,true)
  console.log('PASS portable relative workspace paths, cache identity, offline originals retained, unsupported workspace rejected; no original copies')
}finally{if(app)await app.close()}
