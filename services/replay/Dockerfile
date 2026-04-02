FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY services/replay/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared
COPY services/replay /app/services/replay

ENV PYTHONPATH=/app

CMD ["sh", "-c", "uvicorn services.replay.app:app --host 0.0.0.0 --port ${PORT:-8002}"]
