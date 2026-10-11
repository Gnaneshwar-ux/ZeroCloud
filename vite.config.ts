import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // Relative assets allow a direct local-file launch with no HTTP server.
  base: './',
  plugins: [react()],
})
