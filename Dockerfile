FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Every module the app imports. Missing one means the container builds fine,
# dies on import, and the platform reports a bare 503 with nothing in it.
COPY main.py .
COPY slots.py .
COPY tables.py .
COPY arcade.py .
COPY payments.py .
COPY seed_players.py .
COPY static/ ./static/

# Cloud Run injects the port to listen on and health-checks it. Hardcoding 8000
# means the health check hits nothing and the service never goes live. Default
# to 8080 so a plain `docker run` still works.
ENV PORT=8080
EXPOSE 8080

# Seed the fixed roster on boot, then serve. Seeding is idempotent.
# --ws none: the system websockets package can shadow the version uvicorn wants.
CMD exec sh -c "python seed_players.py || true; uvicorn main:app --host 0.0.0.0 --port ${PORT} --ws none"
