# dbt runner. DuckDB adapter for local mode; swap/add dbt-databricks for the Databricks target.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DBT_PROFILES_DIR=/dbt

RUN pip install "dbt-duckdb>=1.9,<2"

WORKDIR /dbt
COPY dbt/ /dbt/
# Local-mode profile; credentials for other targets come from the environment, never the image.
RUN cp /dbt/profiles.yml.example /dbt/profiles.yml

RUN useradd --system --uid 10001 --no-create-home edupulse \
    && mkdir -p /data /dbt/target /dbt/logs && chown -R edupulse /data /dbt
USER edupulse

CMD ["dbt", "debug"]
