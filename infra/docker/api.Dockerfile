# API image (also reused by the one-shot synthetic-data generator service).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY ingestion ./ingestion
COPY api ./api
COPY rag ./rag
RUN pip install .

# Run as an unprivileged user; /data is the shared warehouse volume.
RUN useradd --system --uid 10001 --no-create-home edupulse \
    && mkdir -p /data && chown edupulse /data
USER edupulse

EXPOSE 8000
CMD ["python", "-m", "api.health"]
