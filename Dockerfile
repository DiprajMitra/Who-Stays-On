# --- Who Stays On? production image ------------------------------------- #
# One service: FastAPI on a single port (8000) that also serves the
# bundled vanilla-JS frontend from /static.
#
# Build (from repo root):
#     docker build -t who-stays-on .
# Run (from repo root):
#     docker compose up --build
# Or directly:
#     docker run --rm -p 8000:8000 who-stays-on
# ------------------------------------------------------------------------- #

FROM python:3.11-slim AS runtime

# Don't write .pyc, send stdout/stderr straight to the terminal logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# ---- 1. Install Python deps (cached at this layer) ----
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# ---- 2. Copy the rest of the source ----
COPY backend/app ./backend/app
COPY static      ./static
COPY sample_input.json ./sample_input.json

# ---- 3. Healthcheck so judges / orchestrators can probe it ----
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; \
        sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status==200 else sys.exit(1)"]

EXPOSE 8000

# Entrypoint: serve from /app/backend so `app.main:app` resolves.
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--app-dir", "backend"]
