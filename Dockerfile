FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ESTONIAN_REPORT_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

RUN groupadd --system app && useradd --system --gid app --home-dir /nonexistent app \
    && mkdir -p /data \
    && chown app:app /data
USER app:app

ENTRYPOINT ["estonian-annual-report-ai-mcp"]
