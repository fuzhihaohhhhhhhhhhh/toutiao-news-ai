# ════════════════════════════════════════════════════════════════
# 前端**生产**镜像 —— 多阶段构建
#
#   阶段 1 (builder)：用 Node 把 Vue 项目编译成静态文件 dist/
#   阶段 2 (runtime)：用 nginx 托管 dist/，并把 /api 反向代理到后端容器
#
# 为什么不在容器里跑 `npm run dev`：
#   Vite 的开发服务器面向本地热更新 —— 不做压缩、没有缓存策略、
#   还会暴露 HMR 调试接口，不适合对外提供服务。
#   开发时在本机跑 `npm run dev` 就好；容器只用于生产部署。
#
# 构建上下文是 frontend/web/，构建时会读同目录的 .dockerignore。
# ════════════════════════════════════════════════════════════════

# ────────────────────────── 阶段 1：构建 ──────────────────────────
FROM node:22-alpine AS builder

WORKDIR /app

# npm 源。默认官方源（适合国际网络）；
# 国内构建时可以换成镜像源加速，实测 registry.npmmirror.com 比官方源快：
#     在仓库根目录的 .env 里写 NPM_REGISTRY=https://registry.npmmirror.com
# 由 docker-compose.prod.yml 的 build.args 传进来。
ARG NPM_REGISTRY=https://registry.npmjs.org

# 先只拷依赖清单：只要它们没变，这一层就能命中缓存，
# 改业务代码时重建不用重新 npm ci（那一步最慢）。
COPY package.json package-lock.json ./
RUN npm ci --registry=${NPM_REGISTRY}

# 再拷源码编译（node_modules 已被 .dockerignore 排除）
COPY . .
RUN npm run build


# ────────────────────────── 阶段 2：运行 ──────────────────────────
FROM nginx:1.27-alpine

# 用项目自带的配置替换 nginx 默认站点配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 只把编译产物搬进来，不留 Node 环境和源码
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

# 健康检查用 alpine 自带的 busybox wget，不必额外装 curl
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
