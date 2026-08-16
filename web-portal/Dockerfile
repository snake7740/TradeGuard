# 构建阶段（R-37：npm ci 按 lockfile 可复现安装，杜绝依赖漂移）
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# 运行阶段：nginx 托管静态资源 + 反代 /api 到 web-api（04 §10.1 双进程形态）
# R-37：基线升 nginx:1.31-alpine（越过 CVE-2026-42945 NGINX Rift 修复线 1.30.1）；
# 配置走 /etc/nginx/templates 模板通道，启动时 envsubst 注入 TG_API_TOKEN（NGINX_ENVSUBST_FILTER=^TG_）
FROM nginx:1.31-alpine
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
