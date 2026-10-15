# Web UI image. The browser calls the API directly, so its address is fixed at build time:
#   docker build --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 -t policy-analytics-web frontend
FROM node:24.20.0-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# The standalone build is a small Node server with only the modules it uses.
FROM node:24.20.0-slim
# Links the published image to its repository in the GitHub Container Registry.
LABEL org.opencontainers.image.source="https://github.com/garygsw/agentic-policy-analytics" \
      org.opencontainers.image.description="Agentic policy analytics: web UI" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node /app/.next/standalone ./
COPY --from=build --chown=node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
