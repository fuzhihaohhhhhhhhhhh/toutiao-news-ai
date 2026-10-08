import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],

  server: {
    // 本机开发时的接口代理。
    //
    // 前端代码里用的是**相对路径**（见 src/config/api.js 的 baseURL），
    // 请求会发到 Vite 自己（5173）。必须有这个代理把它转给后端，
    // 否则开发时所有接口都会 404。
    //
    // 部署到服务器后不需要这个代理 —— 那时由 nginx 承担同样的角色。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // SSE（AI 流式回答）需要长连接，不能让它超时断开
        timeout: 300_000,
      },
    },
  },
})
