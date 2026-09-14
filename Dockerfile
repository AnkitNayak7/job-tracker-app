FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY frontend ./frontend

ENV PYTHONUNBUFFERED=1
WORKDIR /app/backend

# Railway injects $PORT; default to 8080 for local runs.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
