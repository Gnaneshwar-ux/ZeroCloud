import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const dist = resolve(process.cwd(), 'dist')
const indexPath = resolve(dist, 'index.html')
let html = await readFile(indexPath, 'utf8')

const stylesheet = html.match(/<link rel="stylesheet" crossorigin href="\.\/([^"]+)">/)
const moduleScript = html.match(/<script type="module" crossorigin src="\.\/([^"]+)"><\/script>/)

if (!stylesheet || !moduleScript) {
  throw new Error('Expected Vite stylesheet and module tags were not found.')
}

const [css, javascript] = await Promise.all([
  readFile(resolve(dist, stylesheet[1]), 'utf8'),
  readFile(resolve(dist, moduleScript[1]), 'utf8'),
])

const inlineScript = `<script>\n${javascript}\n</script>`
const brandSvg = await readFile(resolve(process.cwd(), 'public/zerocloud.svg'), 'utf8')

html = html
  .replace(/href="\.\/zerocloud.svg"/, () => `href="data:image/svg+xml,${encodeURIComponent(brandSvg)}"`)
  // Callback replacements are required: the minified bundle contains `$&` and
  // other dollar sequences that String.replace would otherwise expand.
  .replace(stylesheet[0], () => `<style>\n${css}\n</style>`)
  // Remove Vite's module tag from the head. A classic inline script can execute
  // from file://, but must be placed after #root so React can mount correctly.
  .replace(moduleScript[0], '')
  .replace('</body>', () => `${inlineScript}\n  </body>`)

await writeFile(indexPath, html, 'utf8')
console.log('Created standalone dist/index.html with inline JavaScript and CSS.')
