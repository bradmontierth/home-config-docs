FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# package copied in as orchestrator/
COPY . /app/orchestrator/

ENV ORCH_DB_PATH=/data/orchestrator.db \
    ANNOUNCE_CACHE_DIR=/data/announce \
    ORCH_HTTP_PORT=8785

EXPOSE 8785
CMD ["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8785"]
