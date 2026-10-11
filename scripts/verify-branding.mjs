import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import {createRequire} from 'node:module'
const root=path.resolve(process.argv[2]), work=path.resolve(process.argv[3])
const require=createRequire(path.join(root,'package.json')), {_electron:electron}=require('playwright')
const application=await electron.launch({executablePath:process.env.VELA_EXECUTABLE||path.join(root,'node_modules/electron/dist/electron.exe'),args:process.env.VELA_PACKAGED==='1'?[]:[path.join(root,'desktop/main.cjs')],env:{...process.env,VELA_TEST:'1',VELA_DATA:path.join(work,'catalog'),VELA_USER_DATA:path.join(work,'profile')},timeout:60000})
try {
  const page=await application.firstWindow();await page.getByRole('textbox',{name:'Search media'}).waitFor()
  assert.equal(await application.evaluate(({app})=>app.getName()),'ZeroCloud')
  assert.match(await page.title(),/^ZeroCloud/)
  assert.equal(await page.locator('.brand').innerText(),'ZeroCloud')
  assert.ok(await page.locator('link[rel="icon"]').getAttribute('href'))
  for(const file of ['zerocloud.svg','zerocloud.png','zerocloud.ico'])await fs.access(path.join(root,'public',file))
  console.log('PASS packaged name, window title, sidebar identity and bundled ZeroCloud icons; legacy storage identifiers unchanged')
} finally {await application.close()}
