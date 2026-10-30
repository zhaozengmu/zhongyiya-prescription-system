FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# PDF 导出依赖（WeasyPrint 需要 pango/cairo）；不装也能跑，只是 PDF 会降级为 HTML 打印
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 静态资源收集（生产由 WhiteNoise / Nginx 托管）
RUN SECRET_KEY=build-only-key python manage.py collectstatic --noinput

EXPOSE 8000

# 生产环境务必通过环境变量注入 DJANGO_SECRET_KEY 与 DATA_ENCRYPTION_KEY
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
