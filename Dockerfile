# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIPELINE_LOG_DIR=/tmp/pipeline-logs
WORKDIR /app

COPY requirements.txt requirements-mysql.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt -r requirements-mysql.txt \
    && groupadd --gid 10001 pipeline \
    && useradd --uid 10001 --gid pipeline --no-create-home pipeline \
    && mkdir -p /data/processed \
    && chown pipeline:pipeline /data/processed

# 只复制独立运行所需源码。
COPY src/__init__.py ./src/
COPY src/order_pipeline/ ./src/order_pipeline/
COPY sql/ ./sql/
COPY scripts/verify_mysql.py ./scripts/verify_mysql.py

USER 10001:10001
ENTRYPOINT ["python", "-m", "src.order_pipeline"]
CMD ["run", "--input", "/data/raw/orders.csv", "--output", "/data/processed/v1", "--database", "/data/processed/orders.sqlite3"]
