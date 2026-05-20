FROM node:20-slim AS frontend-build

WORKDIR /frontend

COPY deployment/frontend/package.json deployment/frontend/vite.config.js deployment/frontend/index.html ./
COPY deployment/frontend/src ./src

RUN npm install --no-audit --no-fund && npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements_api.txt ./

RUN pip install --upgrade pip && \
    pip install -r requirements_api.txt

COPY . .
COPY --from=frontend-build /frontend/dist ./deployment/frontend/dist

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl --fail http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "deployment.api.main:app", "--host", "0.0.0.0", "--port", "8000"]