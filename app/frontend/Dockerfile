# ---------- build stage ----------
FROM node:22-alpine AS builder
WORKDIR /app
ENV CI=true

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

# ---------- runtime stage ----------
# Unprivileged nginx: runs as uid 101 (nginx) and listens on 8080 (a non-root process cannot bind 80).
FROM nginxinc/nginx-unprivileged:1.31.7-alpine AS runtime

# Pull in the Alpine security updates published after the base image was built (keeps `trivy image` clean).
USER root
RUN apk upgrade --no-cache

COPY --chown=101:0 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder --chown=101:0 /app/dist /usr/share/nginx/html

USER 101
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/ > /dev/null || exit 1
