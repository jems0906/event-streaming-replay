FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY services/gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared
COPY services/gateway /app/services/gateway

ENV PYTHONPATH=/app

CMD ["sh", "-c", "uvicorn services.gateway.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
