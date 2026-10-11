import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
const root=path.resolve(process.argv[2] || process.cwd()), work=path.resolve(process.argv[3] || '.vela-test/faces')
const source=process.argv[4] || process.env.VELA_FACE_SAMPLES
if(!source)throw new Error('Provide a face fixture folder as the fourth argument or VELA_FACE_SAMPLES.')
await fs.mkdir(work,{recursive:true})
const child=spawn(process.env.VELA_PYTHON || path.join(root,'.venv/Scripts/python.exe'),['-u',path.join(root,'engine/portable_engine.py'),path.join(work,'catalog')],{windowsHide:true,stdio:['pipe','pipe','pipe']})
const pending=new Map();let id=0;let logs=''
child.stderr.on('data',(data)=>{logs=(logs+data).slice(-12000)})
createInterface({input:child.stdout}).on('line',(line)=>{const message=JSON.parse(line),task=pending.get(message.id);if(task){pending.delete(message.id);if(message.error)task.reject(new Error(message.error));else task.resolve(message.result)}})
child.on('exit',(code)=>{for(const task of pending.values())task.reject(new Error(`Engine exited ${code}: ${logs}`))})
const request=(op,args={})=>new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});child.stdin.write(JSON.stringify({id:key,op,args})+'\n')})
try {
  const scan=await request('scan',{path:source});console.log(`Scanned ${scan.count} linked originals; no copies`)
  await request('faceControl',{enabled:true})
  const deadline=Date.now()+180000
  let people=[],status
  while(Date.now()<deadline) {
    await new Promise((resolve)=>setTimeout(resolve,3000));status=await request('status');console.log(`${status.state}: ${status.done}/${status.total}; ${status.current}`)
    if(status.error) throw new Error(status.error+'\n'+logs)
    people=await request('people')
    if(people.length>=2 || status.done>=12 || status.state==='complete')break
  }
  await request('faceControl',{enabled:false,paused:true})
  assert.ok(status.done>0,'The worker must finish actual photo inference')
  assert.ok(people.length>0,'At least one actual face must be found in the selected real photo folder')
  const person=people[0];await request('renamePerson',{id:person.id,name:'Verification Person'})
  const saved=await request('people');assert.equal(saved.find((p)=>p.id===person.id).name,'Verification Person')
  assert.ok(saved.every((p)=>p.mediaIds.length===p.count))
  const file=await request('resolve',{type:'face',id:person.faceId});assert.ok((await fs.stat(file)).size<50000)
  const storage=await request('storage');assert.equal(storage.originalCopies,0)
  console.log(`PASS real DeepFace: ${people.length} face groups, ${status.done} HEIC/photo inferences, names persisted, group photo filters, tiny face JPEG, ${storage.originalCopies} copied originals`)
  await Promise.race([request('shutdown'),new Promise((resolve)=>setTimeout(resolve,20000))]);child.stdin.end();child.kill()
} catch(error) {child.kill();throw error}
