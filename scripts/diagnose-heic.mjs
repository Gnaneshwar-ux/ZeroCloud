import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import {pathToFileURL} from 'node:url'
const require = createRequire(path.resolve('package.json'))
const { chromium } = require('playwright')
const work = path.resolve(process.argv[2] || '.vela-test/media-validation')
const profiles = (await fs.readdir(work)).filter((entry) => entry.startsWith('profile-')).sort()
const context = await chromium.launchPersistentContext(path.join(work, profiles.at(-1)), { channel: 'chrome', headless: true })
try {
  const page = context.pages()[0]
  page.on('console', (message) => { if (message.type() === 'error' || /HEIF|libheif/i.test(message.text())) console.log(message.text().slice(0, 500)) })
  page.on('pageerror', (error) => console.log('Page error:', error.message))
  await page.goto(pathToFileURL(path.resolve('dist/index.html')).href)
  await page.waitForTimeout(15000)
  console.log(await page.locator('.job-panel').innerText())
  console.log(await page.locator('.error-pill').evaluateAll((elements) => elements.map((element) => element.title)))
  console.log(await page.locator('.media-card').evaluateAll((elements) => elements.map((element) => element.textContent)))
} finally { await context.close() }
