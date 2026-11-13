import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import process from 'node:process'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: process.env.WAYCON_API_PROXY || 'http://127.0.0.1:8002', changeOrigin: true },
    },
  },
})
