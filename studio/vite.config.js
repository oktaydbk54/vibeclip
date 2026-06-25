import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// studio2 is served by FastAPI from /static/studio2 (built output), and the
// SPA itself lives at /studio2. Assets must therefore resolve under
// /static/studio2/. In `npm run dev` we proxy the API + media to the running
// FastAPI server on :8765 so the editor works against real projects.
export default defineConfig({
  base: '/static/studio2/',
  plugins: [react()],
  build: {
    outDir: '../chat/static/studio2',
    emptyOutDir: true,
  },
  server: {
    port: 5174,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
