# syntax=docker/dockerfile:1
# Build context: ./frontend. Static build served by nginx, which also proxies /api to the
# backend service so the browser talks to a single origin.

FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
RUN npm run build


FROM nginx:1.30-alpine AS runtime
LABEL org.opencontainers.image.source="https://github.com/yannicd03/Portolan" \
      org.opencontainers.image.title="portolan-frontend"
COPY --link nginx.conf /etc/nginx/conf.d/default.conf
COPY --link --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
