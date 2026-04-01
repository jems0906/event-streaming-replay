FROM python:3.12-slim

WORKDIR /app

COPY services/replay/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared
COPY services/replay /app/services/replay

ENV PYTHONPATH=/app

CMD ["uvicorn", "services.replay.app:app", "--host", "0.0.0.0", "--port", "8002"]
