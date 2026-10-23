# VidDigest 前端镜像（工单 #63）
#
# 两段式：Node 只出现在构建段，最终镜像里只有静态文件 + nginx。
# 理由：静态站点不需要 Node 运行时，最终镜像因此小一个数量级，
# 攻击面也只剩一个 nginx。
FROM node:22-alpine AS build

# npm 源可换：默认官方源，国内网络可构建时用 --build-arg NPM_REGISTRY=<镜像地址>。
# 不写死某个镜像，是为了不在仓库里替所有人选网络策略。
ARG NPM_REGISTRY=https://registry.npmjs.org

WORKDIR /app

# 锁文件先装：依赖不变时这一层不需要重建
COPY package.json package-lock.json ./
RUN npm ci --registry="${NPM_REGISTRY}"

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=4s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
