# Build the React workbench once, then serve it from FastAPI in production.
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    QUANTLAB_ENV=production QUANT_AGENT_WORKSPACE=/data \
    QUANTLAB_DATABASE_PATH=/data/.quant-agent/quantlab.db
WORKDIR /app
COPY pyproject.toml README.md ./
COPY quant_lab_agent/ ./quant_lab_agent/
RUN pip install --no-cache-dir ".[openai,web]"
COPY --from=frontend-build /src/frontend/dist ./frontend/dist
RUN useradd --create-home --uid 10001 quantlab && mkdir -p /data && chown -R quantlab:quantlab /app /data
USER quantlab
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/api/health')" || exit 1
CMD ["uvicorn", "quant_lab_agent.web.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
