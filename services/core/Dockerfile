FROM python:3.12-slim

WORKDIR /app

COPY services/core/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared
COPY services/core /app/services/core

ENV PYTHONPATH=/app

CMD ["uvicorn", "services.core.app:app", "--host", "0.0.0.0", "--port", "8001"]
