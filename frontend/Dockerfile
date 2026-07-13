# --- build stage ---
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm config set registry https://registry.npmmirror.com && npm install --include=dev
COPY . .
RUN npm run build

# --- serve stage ---
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# 前端 SPA 反代 /api 到后端
RUN printf 'server {\n\
  listen 80;\n\
  location / { root /usr/share/nginx/html; index index.html; try_files $uri $uri/ /index.html; }\n\
  location /api/ { proxy_pass http://backend:8000; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
