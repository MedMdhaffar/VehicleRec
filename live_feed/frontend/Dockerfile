# Stage 1 — build the Vite bundle
# VITE_* vars are baked at build time (import.meta.env); pass them as build args.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL=http://localhost:5034
ARG VITE_CAMERAS=4,1,hik1
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_CAMERAS=$VITE_CAMERAS
RUN npm run build

# Stage 2 — serve with nginx
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Simple SPA config: all paths fall back to index.html
RUN printf 'server {\n  listen 80;\n  root /usr/share/nginx/html;\n  location / {\n    try_files $uri $uri/ /index.html;\n  }\n}\n' \
    > /etc/nginx/conf.d/default.conf
EXPOSE 80
