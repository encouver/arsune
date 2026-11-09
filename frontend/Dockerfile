FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Next.js resolves the rewrites in next.config.ts at build time.
ARG ARSUNE_API_URL=http://api:8540
ENV ARSUNE_API_URL=$ARSUNE_API_URL
ARG ARSUNE_SWARM_URL=http://swarm:8590
ENV ARSUNE_SWARM_URL=$ARSUNE_SWARM_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3540
ENV HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3540
CMD ["node", "server.js"]
