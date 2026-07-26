/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * API base 一律走相对路径 `/api`（见 src/api/client.ts），由本文件的 dev proxy
 * 转发到后端。**禁止在代码里硬编码 http://localhost:8000** —— SPEC §12.1：
 * 硬编码后再改同源代理要动每一个调用点，是唯一有返工代价的一条。
 *
 * 后端默认绑定 127.0.0.1:8000（CLAUDE.md 安全红线：回环绑定替代鉴权）。
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    css: false,
    restoreMocks: true,
    unstubGlobals: true,
  },
})
