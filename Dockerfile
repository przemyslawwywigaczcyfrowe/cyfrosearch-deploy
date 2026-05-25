FROM python:3.11-slim

WORKDIR /app

# Minimal system deps for runtime + google-auth crypto wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python deps from spec
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code + data files
COPY app.py search_engine.py es_mapping.py es_client.py config.py ./
COPY indexer.py ga_updater.py json_search.py ./
COPY brand_aliases.json taxon_aliases.json ./
COPY public ./public

EXPOSE 8000

# Render / Railway / Fly set $PORT; default 8000 for local docker run
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
