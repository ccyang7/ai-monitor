import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // 前端代码里永远写 /api/v1/xxx，跨域交给这里转发。
    // 生产环境由 Nginx 做同样的转发，前端代码一行不用改
    proxy: {
      '/api': {
        // 端口是 8001 不是 8000：本机 8000 被另一个后端（KnowLo）占了，
        // 两个后端都要同时跑，所以这里挪一位。后端启动也要跟着带 --port 8001
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
