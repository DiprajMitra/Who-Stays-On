#!/usr/bin/env bash
# Single-command launcher for the "Who Stays On?" backend.
#
#   ./run.sh                 -> start the API + frontend on http://localhost:8000
#   PORT=9000 ./run.sh       -> start on a different port (overridable)
#   ./run.sh --check         -> verify the install, but don't start the server
#
# Frontend is served from  /          (the static files in ./static)
# API endpoints are at    /api/health (GET), /api/roster (POST),
#                         /api/roster/dryrun (POST), /docs (Swagger UI)
#
# The script is fully offline-friendly: it only needs PyPI on the FIRST run
# (to build the venv and install deps). Subsequent runs use the cached venv.

set -euo pipefail

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT/backend"
VENV_DIR="$BACKEND_DIR/.venv"
STATIC_DIR="$ROOT/static"

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

# --------------------------------------------------------------------------- #
# Optional: --check just verifies the install
# --------------------------------------------------------------------------- #
if [[ "${1:-}" == "--check" ]]; then
  echo "==> --check mode: verifying installation"

  [[ -d "$BACKEND_DIR/app"        ]] || { echo "MISSING: $BACKEND_DIR/app"; exit 1; }
  [[ -f "$BACKEND_DIR/requirements.txt" ]] || { echo "MISSING: requirements.txt"; exit 1; }
  [[ -d "$STATIC_DIR"             ]] || { echo "MISSING: $STATIC_DIR"; exit 1; }
  [[ -f "$STATIC_DIR/index.html"  ]] || { echo "MISSING: static/index.html"; exit 1; }
  [[ -d "$ROOT/evidence"          ]] || { echo "MISSING: $ROOT/evidence"; exit 1; }

  [[ -d "$VENV_DIR" ]] || { echo "No venv; run without --check to create one."; exit 1; }

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -c "import fastapi, uvicorn, pydantic, pulp; print('python ok')"

  echo "OK: install is consistent."
  exit 0
fi

# --------------------------------------------------------------------------- #
# Boot
# --------------------------------------------------------------------------- #
cd "$BACKEND_DIR"

# 1. Create a venv on first run
if [[ ! -d "$VENV_DIR" ]]; then
  echo "==> Creating virtualenv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# 2. Install / upgrade deps quietly
echo "==> Installing backend dependencies (cached after first run)"
pip install --quiet --disable-pip-version-check -r requirements.txt

# 3. Friendly URL banner so judges know where to point a browser
cat <<EOF

  +-------------------------------------------------------+
  |  Who Stays On?                                        |
  |                                                       |
  |  App (frontend): http://localhost:$PORT/             |
  |  Static assets : http://localhost:$PORT/static/      |
  |  API endpoint  : POST http://localhost:$PORT/api/roster
  |  Health check  : GET  http://localhost:$PORT/api/health
  |  Swagger docs  : http://localhost:$PORT/docs         |
  +-------------------------------------------------------+

  Sample input : ./sample_input.json
  Evidence     : ./evidence/README.md

EOF

# 4. Exec the server in the foreground
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level info
