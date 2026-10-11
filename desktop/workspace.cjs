const fs=require('node:fs/promises')
const path=require('node:path')
function reference(file,location) {
  const absolute=path.resolve(location),relative=path.relative(path.dirname(file),absolute)
  return {absolute,...(!path.isAbsolute(relative)?{relative}: {})}
}
function checkReference(value) {
  if(!value||typeof value.absolute!=='string'||value.absolute.length>32768||!path.isAbsolute(value.absolute)||value.absolute.includes('\0'))throw new Error('Invalid workspace location.')
  if(value.relative!==undefined&&(typeof value.relative!=='string'||value.relative.length>32768||path.isAbsolute(value.relative)||value.relative.includes('\0')))throw new Error('Invalid relative workspace location.')
  return value
}
function candidates(file,value) {
  checkReference(value)
  return [...new Set([...(value.relative!==undefined?[path.resolve(path.dirname(file),value.relative)]:[]),path.resolve(value.absolute)])]
}
async function existingLocation(file,value) {
  for(const candidate of candidates(file,value))try{if((await fs.stat(candidate)).isDirectory())return candidate}catch{}
  return candidates(file,value)[0] // Keep offline folders visible, never delete their catalog.
}
async function findLibrary(file,value,id) {
  for(const candidate of candidates(file,value))try{
    const marker=JSON.parse(await fs.readFile(path.join(candidate,'vela-library.json'),'utf8'))
    if(marker.format==='vela-portable'&&marker.version===1&&marker.id===id&&(await fs.stat(path.join(candidate,'catalog.sqlite'))).isFile())return candidate
  }catch{}
  return null
}
async function save(file,{libraryPath,folders,backupPath}) {
  if(folders.length>2000)throw new Error('A workspace can link up to 2,000 source folders.')
  const marker=JSON.parse(await fs.readFile(path.join(libraryPath,'vela-library.json'),'utf8'))
  if(marker.format!=='vela-portable'||marker.version!==1||typeof marker.id!=='string')throw new Error('Invalid saved library identity.')
  const value={format:'zerocloud-workspace',version:1,savedAt:new Date().toISOString(),library:{id:marker.id,location:reference(file,libraryPath)},folders:folders.map(folder=>({id:folder.id,name:folder.name,original:reference(file,folder.location),cache:folder.cacheLocation?reference(file,folder.cacheLocation):null})),backupState:backupPath?reference(file,backupPath):null}
  const temporary=`${file}.${require('node:crypto').randomUUID()}.tmp`
  const serialized=JSON.stringify(value,null,2)
  if(Buffer.byteLength(serialized,'utf8')>2*1024*1024)throw new Error('Workspace paths exceed the supported file size.')
  try{await fs.writeFile(temporary,serialized,'utf8');await fs.rename(temporary,file)}catch(error){await fs.unlink(temporary).catch(()=>{});throw error}
  return {path:file,folders:folders.length}
}
async function read(file) {
  if((await fs.stat(file)).size>2*1024*1024)throw new Error('Workspace file is too large.')
  const value=JSON.parse(await fs.readFile(file,'utf8'))
  if(value.format!=='zerocloud-workspace'||value.version!==1||typeof value.library?.id!=='string'||!Array.isArray(value.folders)||value.folders.length>2000)throw new Error('This is not a supported ZeroCloud workspace file.')
  checkReference(value.library.location)
  const ids=new Set()
  for(const folder of value.folders){if(typeof folder.id!=='string'||!folder.id||ids.has(folder.id))throw new Error('Invalid or duplicate workspace folder identity.');ids.add(folder.id);checkReference(folder.original);if(folder.cache)checkReference(folder.cache)}
  if(value.backupState)checkReference(value.backupState)
  return value
}
module.exports={save,read,findLibrary,existingLocation}
