import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())
  const backendTarget = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8001'

  return {
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
          // 默认 8001：本机 8000 被另一个后端占了，两个后端要同时跑。
          // 要改地址或端口时，复制 .env.example 为 .env.development
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
