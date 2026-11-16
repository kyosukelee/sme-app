# 本番用: 画面と API を1つのイメージにまとめる(SaaS版・専用環境版で共通)
#   docker build -t sme-app .
#   docker run -p 8000:8000 -e DATABASE_URL=... -e AUTH_MODE=supabase sme-app

# 1. 画面をビルドする
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# 2. API にビルド済みの画面を入れる
FROM python:3.12-slim
# PDF 生成(WeasyPrint)に必要なライブラリと日本語フォント
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-ipaexfont-gothic \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STATIC_DIR=/app/static
COPY api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY api/app ./app
COPY --from=web /web/dist ./static
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
