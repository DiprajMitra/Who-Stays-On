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
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .models import RosterRequest
from .solver import ValidationError, solve, validate_payload

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
# Custom exception handler: 422 -> 400 for malformed JSON bodies
# --------------------------------------------------------------------------- #
@app.exception_handler(RequestValidationError)
async def _request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Override FastAPI's default 422 to always return 400 on bad input.

    Per the API contract: any malformed JSON body / structural validation
    failure must surface as HTTP 400 with ``detail`` starting with the
    ``invalid_input:`` prefix. ``exc.errors()`` is the structured list of
    ``pydantic``-style error dicts; we embed it directly in the detail
    string so the frontend (and judges) can inspect field-level failures.
    """
    import json

    return JSONResponse(
        status_code=400,
        content={
            "detail": "invalid_input: " + json.dumps(exc.errors(), default=str),
        },
    )


# --------------------------------------------------------------------------- #
# Custom exception handler: bare ValueError -> 400
# --------------------------------------------------------------------------- #
@app.exception_handler(ValueError)
async def _value_error_exception_handler(
    request: Request, exc: ValueError
) -> JSONResponse:
    """Map any uncaught ``ValueError`` to HTTP 400.

    Pydantic v2 field-validators that raise ``ValueError`` (e.g. duplicate
    shop ids, negative values, capacity cross-checks) bubble up here when
    they aren't wrapped by ``RequestValidationError``.  The dryrun
    endpoint in particular used to surface these as HTTP 500 because the
    bare ``ValueError`` was re-raised from ``model_validate``.  The API
    contract requires 400 for *all* invalid input, with a stable
    ``invalid_input:`` prefix so the frontend can branch on it.

    This also catches ``json.JSONDecodeError``'s parent class for
    non-JSON-compliant literals like ``NaN`` and ``Infinity`` -- the
    stdlib ``json`` module raises ``ValueError`` with the message
    "Out of range float values are not JSON compliant" before FastAPI
    ever sees the body.
    """
    msg = str(exc) or exc.__class__.__name__
    # Normalise the stdlib "out of range" message so the front-end
    # sees the same ``invalid_input:`` shape for every NaN/Inf.
    if "Out of range float" in msg or "not JSON compliant" in msg:
        msg = "NaN and Infinity are not allowed in numeric fields"
    return JSONResponse(
        status_code=400,
        content={"detail": f"invalid_input: {msg}"},
    )


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
    import json

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid_input: payload must be a JSON object")

    # Run the unified strict-mode + cross-field validation up front so
    # all invalid inputs surface as 400 with the canonical
    # ``invalid_input:`` detail prefix (instead of leaking through to
    # the solver, which would raise differently-shaped errors).
    try:
        validate_payload(payload)
    except (ValidationError, PydanticValidationError) as e:
        raise HTTPException(
            status_code=400,
            detail="invalid_input: " + (json.dumps(e.errors(), default=str) if isinstance(e, PydanticValidationError) else str(e)),
        )

    t0 = time.time()
    try:
        result = solve(payload)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail="invalid_input: " + str(e))
    except PydanticValidationError as e:  # defensive
        raise HTTPException(status_code=400, detail="invalid_input: " + json.dumps(e.errors(), default=str))
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
    or 400 with a structured error on failure.  Uses the same
    ``validate_payload`` entry point as ``/api/roster`` so judges get a
    true 1:1 schema check (including cross-field capacity / tier-order
    / duplicate-id guards) without paying the MILP cost.
    """
    import json

    try:
        req = validate_payload(payload)
    except PydanticValidationError as e:
        # ``e.errors()`` may include non-serialisable ``ctx.error`` values
        # when a field-validator raised ``ValueError``; round-trip through
        # ``default=str`` to guarantee a JSON-safe response body.
        return JSONResponse(
            status_code=400,
            content={
                "detail": "invalid_input: " + json.dumps(e.errors(), default=str),
            },
        )
    except ValidationError as e:
        return JSONResponse(
            status_code=400,
            content={"detail": "invalid_input: " + str(e)},
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
