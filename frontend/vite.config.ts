import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev-only: where `/api` is proxied. Override with ATLAS_API_URL when the
// backend runs elsewhere, e.g. `ATLAS_API_URL=http://127.0.0.1:9000 npm run dev`.
const apiTarget = process.env.ATLAS_API_URL || 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
