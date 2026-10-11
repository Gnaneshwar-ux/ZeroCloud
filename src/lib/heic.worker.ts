import { heicTo } from 'heic-to/next'

// Both the decoder and output canvas stay off the UI thread. Vite embeds this
// worker into the standalone page, so it does not need a network connection.
self.onmessage = async (event: MessageEvent<{ blob: Blob; full: boolean }>) => {
  let bitmap: ImageBitmap | undefined
  try {
    bitmap = await heicTo({ blob: event.data.blob, type: 'bitmap' })
    const scale = event.data.full ? 1 : Math.min(1, 720 / Math.max(bitmap.width, bitmap.height))
    const canvas = new OffscreenCanvas(Math.max(1, Math.round(bitmap.width * scale)), Math.max(1, Math.round(bitmap.height * scale)))
    const context = canvas.getContext('2d'); if (!context) throw new Error('HEIC preview renderer unavailable')
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    const blob = await canvas.convertToBlob({ type: event.data.full ? 'image/png' : 'image/jpeg', quality: event.data.full ? 1 : 0.82 })
    self.postMessage({ blob })
  } catch (error) { self.postMessage({ error: error instanceof Error ? error.message : String(error) }) }
  finally { bitmap?.close() }
}
