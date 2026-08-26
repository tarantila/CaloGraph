import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: false,
        headers: {
          'X-Real-IP': '127.0.0.1',
          'X-Forwarded-For': '127.0.0.1',
          'X-Forwarded-Proto': 'http',
          'X-Forwarded-Host': '127.0.0.1:8180',
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['./tests/*.test.ts'],
    coverage: { reporter: ['text', 'html'] },
  },
})
