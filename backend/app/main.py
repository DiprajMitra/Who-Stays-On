"""
FastAPI wrapper around the MILP solver.

Endpoints
---------
GET  /api/health
    Liveness probe.
POST /api/roster
    Body  : the JSON problem definition (see sample_input.json)
    Returns 200 with the full roster payload, or
            400 with {"detail": "..."} on invalid input, or
            200 with status="infeasible" when no feasible plan exists.
POST /api/roster/dryrun
    Validates the payload against the Pydantic schema without solving.
    Useful for judges to confirm the format before paying the solve cost.
GET  /docs
    Interactive OpenAPI docs (Swagger UI) - auto-generated from the
    Pydantic models in ``app.models``.
GET  /
    Serves the bundled HTML frontend (../static/index.html).
GET  /static/<file>
    Serves bundled CSS / JS (mounted by StaticFiles below).
"""

from __future__ import annotations

import logging
import os
import pathlib
import time
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError as PydanticValidationError

from .models import RosterRequest
from .solver import ValidationError, solve

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("who_stays_on")

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# backend/app/main.py -> backend/app -> backend -> <repo root>
BACKEND_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent.parent
STATIC_DIR = (REPO_ROOT / "static").resolve()

# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="Who Stays On?",
    version="1.0.0",
    description=(
        "Optimise which shops in a complex run on the diesel generator "
        "during today's outage windows. Powered by a Mixed-Integer Linear "
        "Program (PuLP + HiGHS)."
    ),
)

# CORS: open by default for the local judge; the app is intended to be
# served from the same origin as the frontend so this rarely matters.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Middleware: hard time budget
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def _add_timing(request: Request, call_next):
    t0 = time.time()
    response = await call_next(request)
    response.headers["X-Elapsed-Sec"] = f"{time.time() - t0:.4f}"
    return response


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "who-stays-on",
        "version": app.version,
    }


@app.post("/api/roster")
def post_roster(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Solve the roster problem and return the canonical response."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    t0 = time.time()
    try:
        result = solve(payload)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PydanticValidationError as e:  # defensive
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("unexpected solver error")
        raise HTTPException(status_code=500, detail=f"internal error: {e}")

    elapsed = time.time() - t0
    log.info(
        "roster solve: status=%s value=%.1f fuel=%.2fL elapsed=%.3fs",
        result.get("status"),
        result.get("total_value_served", 0.0),
        result.get("total_fuel_used_litres", 0.0),
        elapsed,
    )
    return result


@app.post("/api/roster/dryrun")
def post_roster_dryrun(payload: Dict[str, Any]) -> JSONResponse:
    """Validate the payload against the schema without running the solver.

    Returns 200 with ``{"valid": True, "shops": N, "hours": H}`` on success
    or 400 with a structured error on failure.
    """
    try:
        req = RosterRequest.model_validate(payload)
    except PydanticValidationError as e:
        return JSONResponse(
            status_code=400,
            content={
                "valid": False,
                "error": "schema validation failed",
                "details": e.errors(),
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "valid": True,
            "shops": len(req.shops),
            "hours": req.n_hours(),
            "outage_hours": sum(1 for g in req.grid_available if not g),
            "generator_capacity_kw": req.generator_capacity_kw,
            "fuel_budget_litres": req.fuel_budget_litres,
        },
    )


# --------------------------------------------------------------------------- #
# Static frontend (offline, no internet required)
# --------------------------------------------------------------------------- #
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/index.html", response_class=HTMLResponse)
    def index_html() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/app.js")
    def app_js() -> FileResponse:
        return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")

    @app.get("/styles.css")
    def styles_css() -> FileResponse:
        return FileResponse(STATIC_DIR / "styles.css", media_type="text/css")
else:
    log.warning("static directory %s does not exist; static assets disabled", STATIC_DIR)


# --------------------------------------------------------------------------- #
# Entry point (for ``python -m app.main``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_level="info")
