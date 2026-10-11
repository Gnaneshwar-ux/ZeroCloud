import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import {createRequire} from 'node:module'
const require=createRequire(process.cwd()+'/package.json'),ts=require('typescript')
const source=await fs.readFile('src/lib/priorityQueue.ts','utf8')
const code=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText
const {PriorityQueue}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
const queue=new PriorityQueue(),calls=[]
queue.scrollBusy(true)
const visible=queue.enqueue('visible','visible',100,async()=>{calls.push('visible');return true})
const open=queue.enqueue('open','open',1000,async()=>{calls.push('open');return true})
await open;assert.deepEqual(calls,['open'])
queue.scrollBusy(false);await visible;assert.deepEqual(calls,['open','visible'])
queue.pauseBackground(true);queue.scrollBusy(true)
const stopped=queue.enqueue('stopped','stopped',100,async()=>{calls.push('stopped');return true})
queue.scrollBusy(false);await new Promise(r=>setTimeout(r,100));assert.deepEqual(calls,['open','visible'])
queue.pauseBackground(false);await stopped
console.log('PASS: scrolling defers thumbnail work, opening still has priority, scrolling idle never overrides Stop indexing.')
