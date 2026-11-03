/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // The browser calls the backend at same-origin `/api/...`. The dev server (and
  // `vite preview`) strips `/api` and forwards to the backend, as nginx does in
  // the container, so the backend needs no CORS.
  const { API_PROXY_TARGET = 'http://localhost:8000' } = loadEnv(mode, '.', '')

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': {
          target: API_PROXY_TARGET,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
    test: {
      env: { NODE_ENV: 'test' },
      globals: true,
      environment: 'jsdom',
      include: ['src/**/*.test.{ts,tsx}'],
      setupFiles: ['./src/setupTests.ts'],
    },
  }
})
