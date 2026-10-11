const { app, BrowserWindow, dialog, ipcMain, protocol, session, shell } = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const fsp = require('node:fs/promises')
const path = require('node:path')
const { Readable } = require('node:stream')
const { createInterface } = require('node:readline')
const os = require('node:os')
const { pathToFileURL } = require('node:url')
const workspace = require('./workspace.cjs')
const root = path.resolve(__dirname, '..')
protocol.registerSchemesAsPrivileged([{ scheme: 'vela', privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true } }])
let worker, backupWorker, window, dataPath, sequence = 0, stopping = false
const pending = new Map()
const python = process.env.VELA_PYTHON || (app.isPackaged ? path.join(process.resourcesPath, 'python', 'python.exe') : path.join(root, '.venv', 'Scripts', 'python.exe'))
app.setName('ZeroCloud')
app.setAppUserModelId('com.local.zerocloud')
// Keep the established location so branding never resets an existing catalog.
app.setPath('userData', process.env.VELA_USER_DATA || path.join(process.env.LOCALAPPDATA || app.getPath('appData'), 'Vela'))
const ownsInstance = app.requestSingleInstanceLock()
if (!ownsInstance) app.quit()
app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus() } })
const configPath = () => path.join(app.getPath('userData'), 'library-location.json')
function request(op, args = {}, child = worker) {
  return new Promise((resolve, reject) => {
    if (!child || child.exitCode !== null) return reject(new Error('The local engine is unavailable.'))
    const id = ++sequence; pending.set(id, { resolve, reject, worker:child })
    child.stdin.write(JSON.stringify({ id, op, args }) + '\n', (error) => { if (error) { pending.delete(id); reject(error) } })
  })
}
function start(directory) {
  if (!fs.existsSync(python)) throw new Error('Run Setup ZeroCloud.cmd to install the local face engine.')
  dataPath = directory
  worker = spawn(python, ['-u', path.join(root, 'engine', 'portable_engine.py'), dataPath], { windowsHide: true, stdio: ['pipe','pipe','pipe'], env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' } })
  const child = worker
  child.on('spawn', () => { try { os.setPriority(child.pid, os.constants.priority.PRIORITY_BELOW_NORMAL) } catch {} })
  createInterface({ input: worker.stdout }).on('line', (line) => {
    try { const message = JSON.parse(line); const task = pending.get(message.id); if (task) { pending.delete(message.id); if (message.error) task.reject(new Error(message.error)); else task.resolve(message.result) } }
    catch { console.error('Unexpected local engine output') }
  })
  worker.stderr.on('data', (data) => console.error(String(data).trim()))
  worker.on('exit', () => { for (const [id, task] of pending) if (task.worker === child) { task.reject(new Error('Local engine stopped. Restart ZeroCloud.')); pending.delete(id) } })
  worker.on('error', (error) => { for (const [id, task] of pending) if (task.worker === child) { task.reject(error); pending.delete(id) } })
}
async function pickFolder(title, defaultPath) {
  // Test mode uses an isolated user-data path and no real OS dialogs.
  if (process.env.VELA_TEST === '1' && process.env.VELA_TEST_PICK) return process.env.VELA_TEST_PICK
  const result = await dialog.showOpenDialog(window, { title, defaultPath, properties: ['openDirectory', 'createDirectory'] })
  return result.canceled ? undefined : result.filePaths[0]
}
const backupConfig = () => path.join(app.getPath('userData'),'backup-location.json')
let backupStatePath
let backupSwitchPromise
function startBackup() {
  if (backupWorker && backupWorker.exitCode === null) return
  backupStatePath ||= process.env.VELA_BACKUP_STATE || path.join(app.getPath('userData'),'Backup')
  if (!process.env.VELA_BACKUP_STATE && fs.existsSync(backupConfig())) { try {backupStatePath=JSON.parse(fs.readFileSync(backupConfig(),'utf8')).path} catch{} }
  const child=spawn(python,['-u',path.join(root,'engine','backup_service.py'),backupStatePath],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONUNBUFFERED:'1'}})
  backupWorker=child
  child.on('spawn',()=>{try{os.setPriority(child.pid,os.constants.priority.PRIORITY_BELOW_NORMAL)}catch{}})
  createInterface({input:child.stdout}).on('line',(line)=>{try{const message=JSON.parse(line),task=pending.get(message.id);if(task){pending.delete(message.id);if(message.error){task.reject(new Error(message.error))}else{task.resolve(message.result)}}}catch{console.error('Unexpected backup engine output')}})
  child.stderr.on('data',(data)=>console.error(String(data).trim()))
  const fail=(error)=>{for(const[id,task]of pending)if(task.worker===child){task.reject(error);pending.delete(id)}}
  child.on('exit',()=>fail(new Error('Backup engine stopped. Restart ZeroCloud; saved jobs remain resumable.')))
  child.on('error',fail)
}
async function backupRequest(op,args={}) {if(backupSwitchPromise)await backupSwitchPromise;startBackup();return request(op,args,backupWorker)}
const backupAllowed=new Set(['overview','devices','phoneBrowse','inventoryStart','inventoryGet','backupPlan','backupRun','indexStart','taskResume','pause','details','restorePlan','restoreRun','cleanupPlan','cleanupIgnore','cleanupBackup','cleanupAttachProof','cleanupPreview','search','stateBackup'])
async function saveBackupLocation(target) {
  backupStatePath=target
  if(process.env.VELA_TEST!=='1'){await fsp.mkdir(app.getPath('userData'),{recursive:true});await fsp.writeFile(backupConfig(),JSON.stringify({path:target}))}
}
async function backupOperation(op,args) {
  const action=op.slice('backup:'.length)
  if(backupAllowed.has(action))return backupRequest(action,args)
  if(action==='choosePath')return pickFolder(args.purpose==='destination'?'Choose a dedicated backup vault folder':'Choose backup source folder')
  if(action==='cleanupRun') {
    if(args.confirmation!=='DELETE')throw new Error('Type DELETE after reviewing the exact selected files.')
    if(process.env.VELA_TEST!=='1') {
      const choice=await dialog.showMessageBox(window,{type:'warning',title:'Confirm verified source cleanup',message:'Permanently delete the reviewed source files?',detail:'Each source and its backup/duplicate proof will be checked again. Changed or unverifiable files are kept. Restore requires the retained backup. This is not a thumbnail-cache cleanup.',buttons:['Keep files','Delete verified sources'],defaultId:0,cancelId:0,noLink:true})
      if(choice.response!==1)return null
    }
    return backupRequest('cleanupRun',args)
  }
  if(action==='open' || action==='reveal') {
    const locations=[...new Set(args.locations||[])];if(!locations.length||locations.length>500)throw new Error('Select between 1 and 500 catalogued files.')
    if(action==='open' && locations.length>1 && process.env.VELA_TEST!=='1') {
      const choice=await dialog.showMessageBox(window,{type:'question',title:'Open selected files',message:`Open ${locations.length} files in their default apps?`,buttons:['Cancel','Open files'],defaultId:0,cancelId:0,noLink:true});if(choice.response!==1)return null
    }
    const blocked=new Set(['.bat','.cmd','.com','.exe','.js','.jse','.lnk','.msi','.ps1','.reg','.scr','.vbs','.vbe','.wsf','.py','.pyw','.jar','.hta','.html','.htm','.url','.scf','.dll','.cpl'])
    const errors=[];let opened=0
    for(const location of locations){try{const file=await backupRequest('resolveStored',{location});if(action==='reveal'){shell.showItemInFolder(file)}else{if(blocked.has(path.extname(file).toLowerCase()))throw new Error('Direct opening is blocked for executable or active-content files. Reveal the folder instead.');const error=await shell.openPath(file);if(error)throw new Error(error)}opened++}catch(error){errors.push(`${location}: ${error.message}`)}}
    return {opened,errors}
  }
  if(action==='moveState' || action==='importState') {
    const state=await backupRequest('overview');if(state.active)throw new Error('Pause the current disk operation and wait for its current file to finish before changing state.')
    const selected=await pickFolder(action==='importState'?'Select Disk Backup Manager state containing catalog.sqlite3':'Choose an empty dedicated backup-state folder',state.statePath)
    if(!selected)return null
    const source=path.resolve(action==='importState'?selected:state.statePath)
    let target=path.resolve(selected)
    if(action==='moveState' && source.toLowerCase()===target.toLowerCase())return null
    if(action==='moveState' && (target.toLowerCase().startsWith(source.toLowerCase()+path.sep)||source.toLowerCase().startsWith(target.toLowerCase()+path.sep)||target===path.parse(target).root))throw new Error('Choose a separate dedicated empty folder, not a drive root or a nested state folder.')
    if(action==='importState'){
      if(!fs.existsSync(path.join(source,'catalog.sqlite3')))throw new Error('This folder does not contain a Disk Backup Manager catalog.sqlite3.')
      const imports=path.join(app.getPath('userData'),'BackupImports');await fsp.mkdir(imports,{recursive:true});target=await fsp.mkdtemp(path.join(imports,'import-'))
    }else if((await fsp.readdir(target)).length)throw new Error('Choose an empty folder. Existing data will not be overwritten.')
    let releaseSwitch
    backupSwitchPromise=new Promise(resolve=>{releaseSwitch=resolve})
    const previousWorker=backupWorker
    try {
      await request('shutdown',{},backupWorker);backupWorker=undefined
      // Import an active source through SQLite backup, not an inconsistent raw file copy.
      await new Promise((resolve,reject)=>{const copier=spawn(python,['-c',"import sqlite3,sys;from pathlib import Path;s=sqlite3.connect(Path(sys.argv[1]).as_uri()+'?mode=ro',uri=True);d=sqlite3.connect(sys.argv[2]);s.backup(d);assert d.execute('PRAGMA quick_check').fetchone()[0]=='ok';d.close();s.close()",path.join(source,'catalog.sqlite3'),path.join(target,'catalog.sqlite3')],{windowsHide:true,stdio:['ignore','ignore','pipe']});let error='';copier.stderr.on('data',(data)=>error+=data);copier.on('error',reject);copier.on('exit',(code)=>code===0?resolve():reject(new Error(error||'Catalog import failed.')))})
      for(const name of ['logs','staging','inventories','catalog_backups'])if(fs.existsSync(path.join(source,name)))await fsp.cp(path.join(source,name),path.join(target,name),{recursive:true,errorOnExist:true,force:false})
      await saveBackupLocation(target);startBackup();await request('overview',{},backupWorker);return {path:target,previous:state.statePath,imported:action==='importState'}
    }catch(error){await saveBackupLocation(state.statePath);if(backupWorker===previousWorker&&previousWorker.exitCode===null)throw error;if(backupWorker){await request('shutdown',{},backupWorker).catch(()=>{});backupWorker=undefined}startBackup();throw error}
    finally{backupSwitchPromise=undefined;releaseSwitch()}
  }
  throw new Error('Unsupported backup operation.')
}
async function switchLibrary(directory, existing) {
  const manifest = path.join(directory, 'vela-library.json')
  if (existing && !fs.existsSync(manifest)) throw new Error('Select the saved ZeroCloud data folder containing vela-library.json and catalog.sqlite.')
  if (existing && !fs.existsSync(path.join(directory,'catalog.sqlite'))) throw new Error('This backup is missing catalog.sqlite. Select the complete saved library folder.')
  if (!existing && !fs.existsSync(manifest) && (await fsp.readdir(directory)).length) throw new Error('Create an empty folder for your library data. Original media folders must be kept separate.')
  await request('shutdown'); worker = undefined
  start(directory); await request('load')
  if (process.env.VELA_TEST !== '1') { await fsp.mkdir(app.getPath('userData'), { recursive: true }); await fsp.writeFile(configPath(), JSON.stringify({ path: directory })) }
  return true
}
const allowed = new Set(['load','captureDates','previewGet','previewSave','favorite','people','renamePerson','mergePeople','rejectPeople','refindFaces','refindLocations','decodeImage','locations','indexControl','scanStatus','syncFolderCaches','status','faceControl','storage','cacheLimit','backup'])
if (ownsInstance) app.whenReady().then(async () => {
  // The UI has no remote resources. Model setup runs separately in local Python.
  session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}))
  if (process.env.VELA_USER_DATA) app.setPath('userData', process.env.VELA_USER_DATA)
  dataPath = process.env.VELA_DATA || path.join(app.getPath('userData'), 'Library')
  if (!process.env.VELA_DATA && fs.existsSync(configPath())) { try { dataPath = JSON.parse(fs.readFileSync(configPath(), 'utf8')).path } catch {} }
  start(dataPath)
  await request('load')
  protocol.handle('vela', async (req) => {
    try {
      const url = new URL(req.url); const type = url.hostname
      if (!['original', 'face', 'memory','decoded'].includes(type) || req.method !== 'GET') return new Response('Not allowed', { status: 403 })
      const file = type === 'memory' ? await request('memory:resolve', {id:decodeURIComponent(url.pathname.slice(1))}) : await request('resolve', { type, id: decodeURIComponent(url.pathname.slice(1)) })
      const stat = await fsp.stat(file); const extension = path.extname(file).toLowerCase()
      const mime = { '.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp','.avif':'image/avif','.gif':'image/gif','.bmp':'image/bmp','.svg':'image/svg+xml','.heic':'image/heic','.heif':'image/heif','.mp4':'video/mp4','.mov':'video/quicktime','.webm':'video/webm','.m4v':'video/mp4','.mp3':'audio/mpeg','.m4a':'audio/mp4','.wav':'audio/wav','.ogg':'audio/ogg','.pdf':'application/pdf' }[extension] || 'application/octet-stream'
      let start = 0, end = stat.size - 1, status = 200
      const headers = { 'Content-Type': mime, 'Accept-Ranges':'bytes', 'Cache-Control':'no-store', 'Access-Control-Allow-Origin':'*' }
      const range = req.headers.get('range')
      if (range) {
        const match = /^bytes=(\d*)-(\d*)$/.exec(range)
        if (!match || !stat.size || (!match[1] && !match[2])) return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${stat.size}` } })
        if (!match[1]) start = Math.max(0, stat.size - Number(match[2]))
        else { start = Number(match[1]); end = match[2] ? Math.min(end, Number(match[2])) : end }
        if (start > end || start >= stat.size) return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${stat.size}` } })
        status = 206; headers['Content-Range'] = `bytes ${start}-${end}/${stat.size}`
      }
      headers['Content-Length'] = String(Math.max(0, end - start + 1))
      return new Response(stat.size ? Readable.toWeb(fs.createReadStream(file, { start, end })) : null, { status, headers })
    } catch (error) { return new Response(error.message, { status: 404 }) }
  })
  ipcMain.handle('vela:request', async (event, op, args) => {
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw new Error('Untrusted caller')
    if(typeof op==='string' && op.startsWith('backup:'))return backupOperation(op,args||{})
    if(['similarPeople','personFaces','peopleReviews','reviewPerson','reviewFeedback','setMe','categories','assignCategories','organizePeople','peopleOrganization','groupAppearances','editPersonFaces'].includes(op))return request(op,args||{})
    if(op==='deleteMedia' || op==='shareMedia') {
      const files=await request('mediaSelection',args||{})
      for(const file of files){const stat=await fsp.stat(file.path);if(!stat.isFile()||stat.size!==file.byteSize||Math.abs(stat.mtimeMs-file.modified)>2)throw new Error('A selected original changed. Rescan before deleting or sharing it.')}
      if(op==='deleteMedia') {
        const answer=await dialog.showMessageBox(window,{type:'warning',title:'Move selected originals to Recycle Bin?',message:`Delete ${files.length} original photos/videos?`,detail:'This removes the original files from their folders. ZeroCloud uses the system Recycle Bin, never permanent deletion.\n\n'+files.slice(0,8).map(f=>f.name).join('\n'),buttons:['Cancel','Move to Recycle Bin'],defaultId:0,cancelId:0,noLink:true})
        if(answer.response!==1)return null
        const removed=[],failures=[]
        for(const file of files){try{const stat=await fsp.stat(file.path);if(stat.size!==file.byteSize||Math.abs(stat.mtimeMs-file.modified)>2)throw new Error('Original changed');await shell.trashItem(file.path);removed.push(file.id)}catch(error){failures.push(`${file.name}: ${error.message}`)}}
        await request('mediaRemoved',{ids:removed});return {removed,failures}
      }
      const target=await pickFolder('Save selected originals for sharing')
      if(!target)return null
      const copied=[],failures=[]
      for(const file of files){try{await fsp.copyFile(file.path,path.join(target,file.name),fs.constants.COPYFILE_EXCL);copied.push(file.id)}catch(error){failures.push(`${file.name}: ${error.message}`)}}
      if(copied.length)await shell.openPath(target)
      return {copied,failures}
    }
    if (['memory:options','memory:create','memory:surprise','memory:variation','memory:downloadMusic','memory:overview','memory:notice','memory:discover','memory:queue','memory:control','memory:activity','memory:ack'].includes(op)) return request(op,args||{})
    if(op==='saveWorkspace' || op==='openWorkspace') {
      let file=process.env.VELA_TEST==='1'?process.env.VELA_TEST_WORKSPACE:undefined
      if(!file) {
        if(op==='saveWorkspace') {const selected=await dialog.showSaveDialog(window,{title:'Save ZeroCloud workspace',defaultPath:path.join(dataPath,'ZeroCloud.zerocloud-workspace.json'),filters:[{name:'ZeroCloud workspace',extensions:['json']}]});if(selected.canceled)return null;file=selected.filePath}
        else {const selected=await dialog.showOpenDialog(window,{title:'Open ZeroCloud workspace',properties:['openFile'],filters:[{name:'ZeroCloud workspace',extensions:['json']}]});if(selected.canceled)return null;file=selected.filePaths[0]}
      }
      if(op==='saveWorkspace') {
        await request('backup')
        const library=await request('load')
        let backupPath=backupStatePath
        if(!backupPath&&fs.existsSync(backupConfig()))try{backupPath=JSON.parse(await fsp.readFile(backupConfig(),'utf8')).path}catch{}
        if(!backupPath)backupPath=process.env.VELA_BACKUP_STATE||path.join(app.getPath('userData'),'Backup')
        return workspace.save(file,{libraryPath:dataPath,folders:library.folders,backupPath:fs.existsSync(path.join(backupPath,'catalog.sqlite3'))?backupPath:null})
      }
      const saved=await workspace.read(file)
      if(backupWorker&&(await backupRequest('overview')).active)throw new Error('Pause the current backup job before opening another workspace.')
      let target=await workspace.findLibrary(file,saved.library.location,saved.library.id)
      if(!target){const selected=await pickFolder('Locate this workspace’s saved ZeroCloud library');if(!selected)return null;target=await workspace.findLibrary(file,{absolute:selected},saved.library.id);if(!target)throw new Error('The selected data folder is not the library saved in this workspace.')}
      const folders=await Promise.all(saved.folders.map(async folder=>({id:folder.id,location:await workspace.existingLocation(file,folder.original),cacheLocation:folder.cache?await workspace.existingLocation(file,folder.cache):null})))
      const previous=dataPath
      try{if(path.resolve(target).toLowerCase()!==path.resolve(dataPath).toLowerCase())await switchLibrary(target,true);await request('workspaceApply',{folders})}
      catch(error){if(dataPath!==previous)await switchLibrary(previous,true).catch(()=>{});throw error}
      let backupLoaded=false
      if(saved.backupState) {
        const backupPath=await workspace.existingLocation(file,saved.backupState)
        if(fs.existsSync(path.join(backupPath,'catalog.sqlite3'))) {
          let releaseSwitch;backupSwitchPromise=new Promise(resolve=>{releaseSwitch=resolve})
          try{if(backupWorker){await request('shutdown',{},backupWorker);backupWorker=undefined}await saveBackupLocation(backupPath);backupLoaded=true}
          finally{backupSwitchPromise=undefined;releaseSwitch()}
        }
      }
      return {path:file,folders:folders.length,backupLoaded}
    }
    if (allowed.has(op)) return request(op, args)
    if (op === 'listDisks') {
      const roots = Array.from({length:26},(_,index)=>String.fromCharCode(65+index)+':'+path.sep)
      const existing = await Promise.all(roots.map(async (directory)=>{try {const stat=await fsp.stat(directory);return stat.isDirectory()?{path:directory,name:`Drive ${directory.slice(0,2)}`}:null}catch{return null}}))
      return existing.filter(Boolean)
    }
    if (op === 'browseDirectory') {
      if(typeof args.path!=='string' || !path.isAbsolute(args.path))throw new Error('Enter an absolute drive or folder path.')
      const directory=path.resolve(args.path), entries=await fsp.readdir(directory,{withFileTypes:true})
      const visible=entries.filter((entry)=>!entry.isSymbolicLink()&&!['$recycle.bin','system volume information','.vela-cache'].includes(entry.name.toLowerCase())).sort((a,b)=>Number(b.isDirectory())-Number(a.isDirectory())||a.name.localeCompare(b.name))
      const parent=path.dirname(directory)
      return {path:directory,parent:parent===directory?null:parent,total:visible.length,entries:visible.slice(0,1000).map((entry)=>({name:entry.name,path:path.join(directory,entry.name),directory:entry.isDirectory(),kind:path.extname(entry.name).slice(1).toUpperCase()||'File'}))}
    }
    if (op === 'addFolder' || op === 'relinkFolder' || op === 'addDiskFolder') {
      if(op==='addDiskFolder' && (typeof args.path!=='string' || !path.isAbsolute(args.path)))throw new Error('Select a disk or absolute folder path.')
      const directory = op==='addDiskFolder' ? path.resolve(args.path) : await pickFolder(op === 'addFolder' ? 'Select original media folder' : 'Relink original folder')
      if (!directory) return null
      if (op === 'addFolder' || op === 'addDiskFolder') {
        let cachePath = process.env.VELA_TEST === '1' ? process.env.VELA_TEST_CACHE : undefined
        if (process.env.VELA_TEST !== '1') {
          const choice = await dialog.showMessageBox(window,{type:'question',title:'Optional portable folder cache',message:'Where should this folder’s cache and index be saved?',detail:'App storage keeps the default catalog. On this drive creates a small .vela-cache beside the originals. Choose cache folder can import an existing portable cache from another computer. Originals are never copied.',buttons:['App storage','On this drive','Choose cache folder','Cancel'],defaultId:0,cancelId:3,noLink:true})
          if (choice.response===3) return null
          if (choice.response===1) cachePath=path.join(directory,'.vela-cache')
          if (choice.response===2) { cachePath=await pickFolder('Choose an empty or existing ZeroCloud portable folder cache',directory); if(!cachePath)return null }
        }
        if(cachePath)return request('startScan',{path:directory,cachePath})
      }
      return request('startScan', { path: directory, folderId: args.folderId })
    }
    if (op === 'chooseFolderCache') {
      const library=await request('load'), folder=library.folders.find((value)=>value.id===args.folderId)
      if(!folder)throw new Error('Unknown folder')
      const directory=await pickFolder('Choose an empty cache folder, or this folder’s existing portable cache',folder.location)
      return directory ? request('setFolderCache',{folderId:folder.id,cachePath:directory}) : null
    }
    if (op === 'rescanFolder') {
      const library = await request('load'); const folder = library.folders.find((f) => f.id === args.folderId)
      if (!folder) throw new Error('Unknown folder')
      return request('startScan', { path: folder.location, folderId: folder.id })
    }
    if (op === 'moveStorage') {
      const directory = await pickFolder('Choose an empty folder for your current library data', dataPath)
      if (!directory) return null
      const target = path.resolve(directory), source = path.resolve(dataPath)
      if (target.toLowerCase() === source.toLowerCase()) return null
      if (target.toLowerCase().startsWith(source.toLowerCase() + path.sep)) throw new Error('Choose a new empty folder alongside the current library, not inside it. For portable media-folder caches, use Folders → Portable cache.')
      if ((await fsp.readdir(target)).length) throw new Error('Choose an empty folder. No existing data will be overwritten.')
      const current = await request('load')
      await request('faceControl', { enabled:false }); await request('backup'); await request('shutdown'); worker = undefined
      try {
        // Copy only ZeroCloud's known data, never unrelated files placed alongside it.
        for (const name of ['vela-library.json','catalog.sqlite','thumbnails','faces','models','memories']) {
          const file=path.join(source,name)
          if (fs.existsSync(file)) await fsp.cp(file,path.join(target,name),{recursive:true,errorOnExist:true,force:false})
        }
        start(target); await request('rebase',{folders:current.folders}); await request('load')
      }
      catch(error) { if(worker) {await request('shutdown').catch(()=>{});worker=undefined} start(source); throw error }
      if (process.env.VELA_TEST !== '1') { await fsp.mkdir(app.getPath('userData'), { recursive:true }); await fsp.writeFile(configPath(),JSON.stringify({path:target})) }
      return true
    }
    if (op === 'chooseStorage' || op === 'openStorage') {
      const directory = await pickFolder(op === 'chooseStorage' ? 'Choose an empty ZeroCloud data folder' : 'Open existing ZeroCloud data folder', dataPath)
      return directory ? switchLibrary(directory, op === 'openStorage') : null
    }
    throw new Error('Unsupported local operation')
  })
  window = new BrowserWindow({ width:1440,height:960,minWidth:420,minHeight:480,title:'ZeroCloud — Your media, no cloud', icon:path.join(root,'public','zerocloud.png'), backgroundColor:'#f8fafc', show: process.env.VELA_TEST !== '1', webPreferences:{ preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true, webSecurity:true, backgroundThrottling: process.env.VELA_TEST !== '1' } })
  window.webContents.setWindowOpenHandler(() => ({ action:'deny' }))
  window.webContents.on('will-navigate', (event, url) => {
    // Permit only a refresh of our exact local entry page; no other files or sites.
    const destination=event.url||url||''
    if(destination.split('#')[0]!==pathToFileURL(path.join(root,'dist','index.html')).href)event.preventDefault()
  })
  await window.loadFile(path.join(root,'dist','index.html'))
}).catch((error) => { if (process.env.VELA_TEST !== '1') dialog.showErrorBox('ZeroCloud could not start', error.message); console.error(error); app.quit() })
app.on('before-quit', (event) => {
  if ((!worker && !backupWorker) || stopping) return
  event.preventDefault(); stopping = true
  const stop = setTimeout(() => { worker?.kill(); backupWorker?.kill(); app.quit() }, 20000)
  Promise.all([worker?request('shutdown').catch(()=>{}):null,backupWorker?request('shutdown',{},backupWorker).catch(()=>{}):null]).finally(() => { clearTimeout(stop); worker = undefined; backupWorker=undefined; app.quit() })
})
app.on('window-all-closed', () => app.quit())
