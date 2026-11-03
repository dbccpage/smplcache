# Stage 1: Build TypeScript + Vite frontend bundle
FROM node:22-alpine AS web-builder
WORKDIR /build/demo/web
COPY demo/web/package.json demo/web/package-lock.json* ./
RUN npm install
COPY demo/web/ ./
RUN npm run build

# Stage 2: Production Python + FastAPI runtime
FROM python:3.12-slim
WORKDIR /app

ARG RENDER_GIT_COMMIT=""
ARG SMPLCACHE_COMMIT=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    SMPLCACHE_COMMIT=${SMPLCACHE_COMMIT:-${RENDER_GIT_COMMIT}}

COPY pyproject.toml README.md LICENSE ./
COPY smplcache/ ./smplcache/
COPY demo/ ./demo/
COPY --from=web-builder /build/demo/web/dist ./demo/web/dist

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[demo]"

EXPOSE 8000

CMD ["sh", "-c", "uvicorn demo.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
