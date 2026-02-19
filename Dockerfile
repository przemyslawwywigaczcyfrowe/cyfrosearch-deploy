FROM python:3.11-slim

WORKDIR /app

# Minimal system deps
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Python deps — only what demo_server needs (lean image ~150MB)
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    "elasticsearch[async]>=8.12.0,<9.0" \
    "orjson>=3.9.0"

# Copy demo server + frontend assets + data files
COPY demo_server.py .
COPY demo.html .
COPY static/ ./static/
COPY sales_data.json .

EXPOSE 8000

# Railway/Render dynamically set $PORT
CMD ["sh", "-c", "uvicorn demo_server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
