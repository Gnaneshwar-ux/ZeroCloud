import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import path from 'node:path'
import { createServer } from 'node:http'

const arguments_ = process.argv.slice(2).filter((value) => value !== '--')
const root = path.resolve(arguments_[0] ?? process.cwd())
const work = path.resolve(arguments_[1] ?? '.vela-test')
const requireProject = createRequire(path.join(root, 'package.json'))
const ts = requireProject('typescript')
const { chromium } = requireProject('playwright')
await fs.mkdir(work, { recursive: true })
const queueSource = ts.transpileModule(await fs.readFile(path.join(root, 'src/lib/priorityQueue.ts'), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText
const { PriorityQueue } = await import(`data:text/javascript;base64,${Buffer.from(queueSource).toString('base64')}`)
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const order = []
const queue = new PriorityQueue()
queue.pauseBackground(true)
const background = queue.enqueue('background', 'Background', 1, async () => { order.push('background') })
const near = queue.enqueue('near', 'Near', 30, async () => { order.push('near') })
const visible = queue.enqueue('visible', 'Visible', 100, async () => { order.push('visible') })
const opened = queue.enqueue('opened', 'Opened', 1000, async () => { order.push('opened') })
await opened; assert.deepEqual(order, ['opened'])
queue.pauseBackground(false); await Promise.all([background, near, visible]); assert.deepEqual(order, ['opened', 'visible', 'near', 'background'])
let runs = 0
const once = queue.enqueue('same', 'One decode', 1, async () => { runs++; return 7 })
assert.equal(queue.enqueue('same', 'Same decode', 100, async () => 8), once)
assert.equal(await once, 7); assert.equal(runs, 1)
let entered
const started = new Promise((resolve) => { entered = resolve })
let attempts = 0
const interrupted = queue.enqueue('interrupt', 'Poster', 1, async (signal) => {
  attempts++
  if (attempts === 1) { entered(); await new Promise((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Paused', 'AbortError')), { once: true })) }
  return 'resumed'
})
await started; queue.pauseBackground(true); await delay(70); assert.equal(attempts, 1)
queue.pauseBackground(false); assert.equal(await interrupted, 'resumed'); assert.equal(attempts, 2)
console.log('PASS scheduler: opened > visible > nearby > background; deduplication; interruption and resume')

const context = await chromium.launchPersistentContext(path.join(work, `profile-${Date.now()}`), { channel: 'chrome', headless: true, viewport: { width: 1440, height: 900 }, args: ['--autoplay-policy=no-user-gesture-required'] })
let page = context.pages()[0]
const errors = []
let testServer
const closeServer = (server) => new Promise((resolve) => { server.close(resolve); server.closeAllConnections() })
page.on('pageerror', (error) => errors.push(error.message))
try {
  await page.goto(pathToFileURL(path.join(root, 'dist/index.html')).href)
  await page.getByRole('button', { name: 'Add folder', exact: true }).first().waitFor()
  const media = await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = 800; canvas.height = 500
    const draw = canvas.getContext('2d'); draw.fillStyle = '#779bc5'; draw.fillRect(0, 0, 800, 500); draw.fillStyle = '#f4dfad'; draw.fillRect(200, 140, 250, 180)
    const image = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
    const videoCanvas = document.createElement('canvas'); videoCanvas.width = 320; videoCanvas.height = 180
    const v = videoCanvas.getContext('2d'); v.fillStyle = '#519879'; v.fillRect(0, 0, 320, 180)
    const stream = videoCanvas.captureStream(15)
    const recorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp8' })
    const chunks = []; recorder.ondataavailable = (event) => chunks.push(event.data)
    const stopped = new Promise((resolve) => { recorder.onstop = resolve })
    recorder.start(); let frame = 0
    const animation = setInterval(() => { v.fillStyle = '#519879'; v.fillRect(0, 0, 320, 180); v.fillStyle = '#fff'; v.fillRect((frame++ * 5) % 280, 70, 40, 40) }, 65)
    await new Promise((resolve) => setTimeout(resolve, 1800)); recorder.stop(); await stopped; clearInterval(animation); stream.getTracks().forEach((track) => track.stop())
    const video = new Blob(chunks, { type: 'video/webm' })
    return { image: Array.from(new Uint8Array(await image.arrayBuffer())), video: Array.from(new Uint8Array(await video.arrayBuffer())) }
  })
  const folderA = path.join(work, 'Album-A'); const folderB = path.join(work, 'Album-B')
  await fs.mkdir(path.join(folderA, 'nested'), { recursive: true }); await fs.mkdir(folderB, { recursive: true })
  await fs.writeFile(path.join(folderA, 'first.png'), Buffer.from(media.image))
  await fs.writeFile(path.join(folderA, 'nested/first.png'), Buffer.from(media.image))
  await fs.writeFile(path.join(folderA, 'movie.webm'), Buffer.from(media.video))
  await fs.writeFile(path.join(folderB, 'second.png'), Buffer.from(media.image))
  await page.locator('input[type=file]').evaluate((input) => input.setAttribute('webkitdirectory', ''))
  await page.locator('input[type=file]').setInputFiles(folderA)
  await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '3 / 3', { timeout: 45000 })
  assert.equal(await page.getByRole('listitem').count(), 3)
  const favorites = page.getByRole('button', { name: 'Favorite first.png', exact: true })
  await favorites.first().click(); await page.getByRole('button', { name: 'Remove first.png from favorites', exact: true }).waitFor()
  async function catalogStats() {
    return page.evaluate(async () => {
      const db = await new Promise((resolve, reject) => { const request = indexedDB.open('vela-local-library-v1'); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error) })
      const read = (store) => new Promise((resolve, reject) => { const request = db.transaction(store).objectStore(store).getAll(); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error) })
      const [folders, records, previews, sources] = await Promise.all(['folders', 'media', 'previews', 'sources'].map(read)); db.close()
      return { folders: folders.length, items: records.length, favorites: records.filter((item) => item.favorite).length, sources: sources.length, copiedSources: sources.filter((source) => source.file).length, linkedSources: sources.filter((source) => source.handle && !source.file).length, previews: previews.map((item) => ({ key: item.key, size: item.blob.size, version: item.version })) }
    })
  }
  const before = await catalogStats(); assert.equal(before.items, 3); assert.equal(before.favorites, 1); assert.equal(before.previews.length, 3); assert.equal(before.copiedSources,0)
  await page.reload(); await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '3 / 3')
  const refreshed = await catalogStats(); assert.deepEqual(refreshed.previews, before.previews); assert.equal(refreshed.favorites, 1)
  await page.waitForFunction(() => [...document.querySelectorAll('.media-preview img')].filter((image) => image.naturalWidth > 0).length >= 2)
  assert.ok(await page.locator('.media-preview img').count() >= 2)
  await page.locator('input[type=file]').setInputFiles(folderA)
  await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '3 / 3' && !document.querySelector('.index-status.running'))
  assert.equal((await catalogStats()).items, 3)
  await page.locator('input[type=file]').setInputFiles(folderB)
  await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '4 / 4')
  assert.equal((await catalogStats()).folders, 2)
  await page.getByRole('button', { name: 'Open movie.webm', exact: true }).click()
  await page.locator('.viewer video').waitFor()
  await page.waitForFunction(() => { const player = document.querySelector('.viewer video'); return player && player.videoWidth === 320 && player.currentTime > 0.25 })
  const playing = await page.locator('.viewer video').evaluate((player) => ({ width: player.videoWidth, height: player.videoHeight, time: player.currentTime, source: player.src, quality: player.getVideoPlaybackQuality?.() }))
  assert.equal(playing.width, 320); assert.equal(playing.height, 180); assert.ok(playing.source.startsWith('blob:'))
  await page.getByRole('button', { name: 'Close viewer', exact: true }).click()
  await page.getByRole('button', { name: 'Open first.png', exact: true }).first().click()
  await page.waitForFunction(() => document.querySelector('.viewer img')?.naturalWidth === 800)
  await page.getByRole('button', { name: '100% view', exact: true }).click(); assert.equal(Math.round(await page.locator('.full-image').evaluate((image)=>image.getBoundingClientRect().width)),800)
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Collapse navigation', exact: true }).click()
  await page.reload(); await page.getByRole('button', { name: 'Expand navigation', exact: true }).waitFor()
  const rail = await page.locator('.sidebar').evaluate((element) => ({ width: element.getBoundingClientRect().width, client: element.clientHeight, scroll: element.scrollHeight }))
  assert.equal(rail.width, 76); assert.equal(rail.scroll, rail.client)
  await page.screenshot({ path: path.join(work, 'library.png'), fullPage: false })
  console.log('PASS standalone file launch: import, refresh, persistent cache/favorites, nested path identity, folder merge, original video playback, full-size image, rail persistence')

  // Mutating a fixture tests version invalidation without touching real media.
  await delay(1100); await fs.writeFile(path.join(folderA, 'first.png'), Buffer.from(media.image))
  await page.locator('input[type=file]').setInputFiles(folderA)
  await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '4 / 4' && !document.querySelector('.index-status.running'))
  await page.waitForFunction(async () => {
    const db = await new Promise((resolve) => { const request = indexedDB.open('vela-local-library-v1'); request.onsuccess = () => resolve(request.result) })
    const count = await new Promise((resolve) => { const request = db.transaction('previews').objectStore('previews').count(); request.onsuccess = () => resolve(request.result) }); db.close(); return count === 4
  })
  const changed = await catalogStats()
  assert.equal(changed.items, 4); assert.equal(changed.favorites, 1)
  assert.ok(!changed.previews.some((preview) => preview.key === before.previews.find((preview) => preview.key.includes('/first.png@') && !preview.key.includes('/nested/'))?.key))
  assert.equal(changed.previews.length, 4)
  console.log('PASS changed file invalidates its thumbnail and preserves favorites')
  await page.close(); page = await context.newPage(); page.on('pageerror', (error) => errors.push(error.message))
  await page.goto(pathToFileURL(path.join(root, 'dist/index.html')).href)
  await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '4 / 4')
  assert.equal((await catalogStats()).folders, 2)
  assert.deepEqual(errors, [])
  console.log('PASS reopening the same browser profile restores both folders with zero page errors')

  // This sample stays on this machine and is never uploaded.
  const heicFolder = path.join(work, 'HEIC-test'); await fs.mkdir(heicFolder, { recursive: true })
  if(!process.env.VELA_HEIC_SAMPLE)throw new Error('Set VELA_HEIC_SAMPLE to a local HEIC fixture.')
  await fs.copyFile(process.env.VELA_HEIC_SAMPLE, path.join(heicFolder, 'sample.HEIC'))
  await page.locator('input[type=file]').setInputFiles(heicFolder)
  await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '5 / 5', null, { timeout: 90000 })
  await page.getByRole('button', { name: 'Open sample.HEIC', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.viewer img')?.naturalWidth > 1000, null, { timeout: 90000 })
  const fullSize = await page.locator('.viewer img').evaluate((image) => [image.naturalWidth, image.naturalHeight])
  const heicCache = await catalogStats(); assert.equal(heicCache.previews.filter((preview) => preview.key.endsWith(':full')).length, 0)
  await page.getByRole('button', { name: 'Close viewer', exact: true }).click()
  const reopenTime = Date.now()
  await page.getByRole('button', { name: 'Open sample.HEIC', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.viewer img')?.naturalWidth > 1000)
  assert.deepEqual(await page.locator('.viewer img').evaluate((image) => [image.naturalWidth, image.naturalHeight]), fullSize)
  assert.equal((await catalogStats()).previews.filter((preview) => preview.key.endsWith(':full')).length, 0)
  console.log(`PASS real HEIC thumbnail and full-size ${fullSize.join('x')} preview; repeat open reused cache in ${Date.now() - reopenTime}ms`)
  await page.keyboard.press('Escape'); await page.reload()
  await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '5 / 5')
  await page.locator('input[type=file]').setInputFiles(heicFolder)
  await page.waitForFunction(() => !document.querySelector('.index-status.running'))
  await page.getByRole('button', { name: 'Open sample.HEIC', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.viewer img')?.naturalWidth > 1000)
  assert.equal((await catalogStats()).previews.filter((preview) => preview.key.endsWith(':full')).length, 0)
  console.log('PASS HEIC original reconnects after refresh; no full-resolution disk cache or original copies')
  assert.deepEqual(errors, [])
  await page.keyboard.press('Escape')

  // Browser directory handles need a supported secure origin. The release's
  // serverless file mode is already covered above. This loopback server exists
  // only during this API test and exposes only the built app, never media.
  const standalonePage = page
  const release = await fs.readFile(path.join(root, 'dist/index.html'))
  testServer = createServer((_request, response) => { response.writeHead(200, { 'Content-Type': 'text/html' }); response.end(release) })
  await new Promise((resolve) => testServer.listen(0, '127.0.0.1', resolve))
  page = await context.newPage(); page.on('pageerror', (error) => errors.push(error.message))
  await page.goto(`http://127.0.0.1:${testServer.address().port}/`)
  await page.waitForFunction(() => !document.querySelector('.add-source')?.disabled)
  // Use a real browser file handle in this isolated origin to test saved links.
  await page.evaluate(async (bytes) => {
    const root = await navigator.storage.getDirectory()
    const folder = await root.getDirectoryHandle('Linked-fixture', { create: true })
    const file = await folder.getFileHandle('linked.png', { create: true })
    const writer = await file.createWritable(); await writer.write(new Uint8Array(bytes)); await writer.close()
    window.showDirectoryPicker = async () => folder
  }, media.image)
  await page.getByRole('button', { name: 'Add folder', exact: true }).first().click()
  await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '1 / 1')
  assert.equal((await catalogStats()).folders, 1)
  assert.equal((await catalogStats()).linkedSources, 1)
  await page.reload(); await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '1 / 1')
  await page.getByRole('button', { name: 'Open linked.png', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.viewer img')?.naturalWidth === 800)
  await page.keyboard.press('Escape')
  console.log('PASS stored file handles restore linked originals after refresh without copying them')
  await page.close(); page = standalonePage; await page.bringToFront()
  await closeServer(testServer); testServer = undefined

  // A large saved catalog verifies viewport rendering independently of scanning.
  await page.evaluate(async (bytes) => {
    const db = await new Promise((resolve) => { const request = indexedDB.open('vela-local-library-v1'); request.onsuccess = () => resolve(request.result) })
    const get = (store) => new Promise((resolve) => { const request = db.transaction(store).objectStore(store).getAll(); request.onsuccess = () => resolve(request.result) })
    const originals = await get('media'); const previews = await get('previews'); const sources = await get('sources')
    const original = originals.find((item) => item.name === 'second.png')
    const preview = previews.find((item) => item.mediaId === original.id && item.type === 'thumbnail')
    const source = sources.find((item) => item.id === original.id)
    const tx = db.transaction(['folders', 'media', 'previews', 'sources'], 'readwrite')
    const done = new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = () => reject(tx.error) })
    tx.objectStore('folders').put({ id: 'bulk-fixture', name: 'Large-library-test', mode: 'copied', count: 1200, addedAt: Date.now() })
    for (let index = 0; index < 1200; index++) {
      const id = `bulk-fixture/${String(index).padStart(4, '0')}.png`
      tx.objectStore('media').put({ ...original, id, folderId: 'bulk-fixture', name: `bulk-${index}.png`, relativePath: `bulk-${index}.png`, favorite: false })
      // Explicitly test compatibility with old browser catalogs containing File copies.
      tx.objectStore('sources').put({ ...source, id, file:new File([new Uint8Array(bytes)],original.name,{lastModified:original.modified}) })
      tx.objectStore('previews').put({ ...preview, key: `${id}@${original.version}:thumbnail`, mediaId: id })
    }
    await done; db.close()
  },media.image)
  const restoreStart = Date.now()
  await page.reload()
  await page.waitForFunction(() => document.querySelector('.job-panel small')?.textContent === '1,205 / 1,205')
  assert.ok(await page.getByRole('listitem').count() < 100)
  const restoreMs = Date.now() - restoreStart
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  await page.getByRole('button', { name: 'Open bulk-1199.png', exact: true }).waitFor()
  assert.ok(await page.getByRole('listitem').count() < 100)
  await page.getByRole('button', { name: 'Open bulk-1199.png', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.viewer img')?.naturalWidth === 800)
  console.log(`PASS 1,205-item saved catalog restored in ${restoreMs}ms with fewer than 100 mounted cards; last item opens after scrolling`)
  await page.keyboard.press('Escape')
  assert.deepEqual(errors, [])
} catch (error) {
  console.error('Browser errors:', errors)
  console.error('Last processing status:', await page.locator('.job-panel').innerText().catch(() => 'unavailable'))
  console.error('Preview errors:', await page.locator('.error-pill').evaluateAll((elements) => elements.map((element) => element.title)).catch(() => []))
  throw error
} finally { await context.close(); if (testServer) await closeServer(testServer) }
