# 웹 화면 (Next.js, frontend/) 이미지. 빌드 컨텍스트는 frontend/ (frontend/.dockerignore).
#   docker build -f infra/docker/web.Dockerfile -t rebom-web frontend
#   docker run --rm -p 3000:3000 rebom-web   → http://localhost:3000
# 검색 API 는 같은 주소의 /api 로 부른다(k3s Traefik 이 /api 를 떼고 api 로 넘김).
# NEXT_PUBLIC_ 값은 빌드할 때 박히므로, 주소를 바꾸려면 다시 빌드한다.

FROM node:22-alpine AS build
RUN npm install -g pnpm@10.34.3
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
# public/ 의 이미지·폰트는 Git LFS 파일이다. LFS 를 받지 않고 체크아웃하면 포인터 텍스트만 들어와 화면에서 깨지므로 여기서 막는다
RUN if grep -rlq '^version https://git-lfs.github.com/spec/v1' public; then \
      echo "public/ 에 Git LFS 포인터 파일이 있습니다. git lfs pull (CI 는 checkout lfs: true) 후 다시 빌드하세요:" >&2; \
      grep -rl '^version https://git-lfs.github.com/spec/v1' public | head -5 >&2; exit 1; \
    fi
ARG NEXT_PUBLIC_API_BASE_URL=/api
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL     NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:22-alpine
ENV NODE_ENV=production     NEXT_TELEMETRY_DISABLED=1     HOSTNAME=0.0.0.0     PORT=3000
WORKDIR /app
# output: "standalone" 결과만 담는다 (node_modules 전체 없이)
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
