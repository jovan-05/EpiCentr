# Keystone API (FastAPI) for deployment, e.g. on Railway next to a FalkorDB service.
# Config comes from environment variables: FALKORDB_HOST, FALKORDB_PORT, (FALKORDB_PASSWORD), OPENROUTER_API_KEY,
# LLM_MODELS. On first boot with an empty graph, the API loads + derives data/northwind (about a second).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY data/northwind ./data/northwind

# Railway (and most hosts) set $PORT; 8000 locally.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
