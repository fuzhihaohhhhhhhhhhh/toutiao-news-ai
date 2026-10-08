/**
 * API 配置文件
 *
 * baseURL 默认是**空串**，也就是所有请求都走"当前站点"的相对路径：
 *     `${baseURL}/api/user/login`   ->   `/api/user/login`
 *
 * 为什么用相对路径（这是为了能部署到服务器）：
 *   原先写死的 `http://127.0.0.1:8000`，在浏览器里的含义是
 *   **访问者自己那台电脑**，而不是服务器。一旦部署到服务器，
 *   前端页面的所有请求都会打到访问者本机的 8000 端口 —— 必然连接失败。
 *   改成相对路径后，无论部署在什么 IP、什么域名、有没有 HTTPS，
 *   前端代码都不用改；而且与页面同源，天然没有跨域问题。
 *
 * 两种情况分别由谁接管 /api：
 *     本机开发   -> vite.config.js 的 server.proxy 转发到 127.0.0.1:8000
 *     服务器生产 -> frontend/web/nginx.conf 反向代理到 backend 容器
 *
 * 如果确实需要指向另一个域名上的后端，构建时传环境变量即可：
 *     VITE_API_BASE_URL=https://api.example.com npm run build
 */
export const apiConfig = {
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
}
