import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// In development (`npm run dev`) API calls are forwarded to the Python backend.
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', ws: true },
    },
  },
})
