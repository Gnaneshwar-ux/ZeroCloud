import fs from 'node:fs/promises'
import path from 'node:path'
import {createRequire} from 'node:module'
import {execFileSync} from 'node:child_process'
const root=process.cwd(),require=createRequire(path.join(root,'package.json')), {chromium}=require('playwright')
const svg=await fs.readFile(path.join(root,'public/zerocloud.svg'),'utf8')
const browser=await chromium.launch({channel:'chrome',headless:true})
try {
  const page=await browser.newPage({viewport:{width:256,height:256},deviceScaleFactor:1})
  await page.setContent(`<html><body style="margin:0;background:transparent">${svg}</body></html>`)
  await page.screenshot({path:path.join(root,'public/zerocloud.png'),omitBackground:true})
} finally {await browser.close()}
execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-c',`from PIL import Image;from pathlib import Path;p=Path(${JSON.stringify(root)});Image.open(p/'public/zerocloud.png').save(p/'public/zerocloud.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])`],{windowsHide:true})
console.log('Generated ZeroCloud window and Windows shortcut icons from the bundled vector logo.')
