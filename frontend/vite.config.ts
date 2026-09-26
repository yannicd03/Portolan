import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the API runs separately (`portolan serve`); in Docker nginx proxies /api.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': process.env.PORTOLAN_API_URL ?? 'http://127.0.0.1:8000',
    },
  },
})
