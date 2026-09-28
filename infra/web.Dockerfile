FROM node:22-bookworm-slim AS deps
WORKDIR /app
RUN npm install --global pnpm@11.19.0
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile

FROM deps AS dev
COPY apps/web apps/web
RUN mkdir -p /app/apps/web/.next && chown -R node:node /app/apps/web
USER node
ENV NODE_ENV=development NEXT_TELEMETRY_DISABLED=1 API_INTERNAL_URL=http://api:8000
CMD ["pnpm", "--filter", "web", "exec", "next", "dev", "--hostname", "0.0.0.0"]

FROM deps AS build
COPY apps/web apps/web
ENV NEXT_TELEMETRY_DISABLED=1
ENV API_INTERNAL_URL=http://api:8000
RUN pnpm build

FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000 NEXT_TELEMETRY_DISABLED=1 API_INTERNAL_URL=http://api:8000
COPY --from=build --chown=node:node /app/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /app/apps/web/.next/static ./apps/web/.next/static
USER node
CMD ["node", "apps/web/server.js"]
