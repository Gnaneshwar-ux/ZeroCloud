// Offline Windows folder build: app/runtime only. No catalog or original media.
import fs from 'node:fs/promises'
import path from 'node:path'
import {execFileSync} from 'node:child_process'
const root=process.cwd()
const metadata=JSON.parse(await fs.readFile(path.join(root,'package.json'),'utf8'))
const destination=path.resolve(process.argv[2] || path.join(root,'release',`ZeroCloud-win-x64-${Date.now()}`))
try {await fs.access(destination);throw new Error('Package destination must not already exist; no existing files are overwritten.')} catch(error) {if(error.code!=='ENOENT')throw error}
await fs.mkdir(destination,{recursive:true})
await fs.cp(path.join(root,'node_modules/electron/dist'),destination,{recursive:true})
await fs.rename(path.join(destination,'electron.exe'),path.join(destination,'ZeroCloud.exe'))
const resources=path.join(destination,'resources')
// Electron's default app must not take precedence over our explicit app folder.
const defaultArchive=path.join(resources,'default_app.asar')
try {await fs.rename(defaultArchive,path.join(resources,'electron-default-app.backup'))} catch(error) {if(error.code!=='ENOENT')throw error}
const appDirectory=path.join(resources,'app');await fs.mkdir(appDirectory,{recursive:true})
await fs.writeFile(path.join(appDirectory,'package.json'),JSON.stringify({name:metadata.name,productName:'ZeroCloud',version:metadata.version,main:'desktop/main.cjs'}))
for(const name of ['desktop','dist','engine','docs','public','third-party'])await fs.cp(path.join(root,name),path.join(appDirectory,name),{recursive:true,filter:(source)=>!source.includes('__pycache__')})
const modelSeed=process.env.VELA_MODEL_SEED||path.join(root,'models')
if(modelSeed) {
  const weights=path.join(appDirectory,'models','.deepface','weights');await fs.mkdir(weights,{recursive:true})
  for(const name of ['face_recognition_sface_2021dec.onnx','haarcascade_eye.xml','haarcascade_frontalface_default.xml','face_detection_yunet_2023mar.onnx'])await fs.copyFile(path.join(modelSeed,'.deepface','weights',name),path.join(weights,name))
}
const python=path.join(root,'.venv/Scripts/python.exe')
const base=JSON.parse(execFileSync(python,['-c','import sys,json;print(json.dumps(sys.base_prefix))'],{encoding:'utf8',windowsHide:true}))
const pythonDestination=path.join(resources,'python')
const skip=new Set(['site-packages','__pycache__','Scripts','include','libs','share','tcl','Doc','Tools','.venv','venv'])
await fs.cp(base,pythonDestination,{recursive:true,filter:(source)=>source===base || !path.relative(base,source).split(path.sep).some((part)=>skip.has(part))})
await fs.cp(path.join(root,'.venv/Lib/site-packages'),path.join(pythonDestination,'Lib/site-packages'),{recursive:true,filter:(source)=>!source.includes('__pycache__')})
const check=execFileSync(path.join(pythonDestination,'python.exe'),['-c','import importlib.util,sqlite3,pythoncom,win32com.client,rich; from PIL import Image; from win32com.shell import shell; assert importlib.util.find_spec("deepface"); assert importlib.util.find_spec("pillow_heif"); assert importlib.util.find_spec("onnxruntime"); print("Runtime OK: imaging, face inference and local Windows backup support; HEIC may use the Windows codec fallback")'],{encoding:'utf8',windowsHide:true,env:{...process.env,DEEPFACE_BACKEND_ENGINE:'onnx'}})
console.log(check.trim())
// Keep Electron's executable byte-for-byte intact. A shortcut carries the custom icon.
execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',"$packageDirectory=$env:ZEROCLOUD_PACKAGE; $shortcut=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $packageDirectory 'ZeroCloud.lnk')); $shortcut.TargetPath=Join-Path $packageDirectory 'ZeroCloud.exe'; $shortcut.WorkingDirectory=$packageDirectory; $shortcut.IconLocation=(Join-Path $packageDirectory 'resources\\app\\public\\zerocloud.ico')+',0'; $shortcut.Description='ZeroCloud - Your media, no cloud'; $shortcut.Save()"],{windowsHide:true,env:{...process.env,ZEROCLOUD_PACKAGE:destination}})
console.log(`Standalone Windows application: ${path.join(destination,'ZeroCloud.exe')}`)
console.log('No original photos, videos, personal catalog, or media cache are included.')
