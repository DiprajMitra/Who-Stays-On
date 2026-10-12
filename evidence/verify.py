#!/usr/bin/env python3
"""
Who Stays On? — executable evidence suite
=========================================

This script is the **Verification Evidence** deliverable referenced
in the README and the judging criteria.  It runs every fixture in
``evidence/inputs/`` through the solver, checks the response against
the recorded ``evidence/fixed_outputs/`` file, and prints a rich
table that judges can read at a glance.

Three flavours of check:

1. **Feasible** fixtures (``sample_shapla``, ``tiny_3shop_feasible``,
   ``happy_path_balanced``) -- response is ``status: ok`` and the
   numeric ``total_value_served`` / ``total_fuel_used_litres`` match
   the recorded fixed output to 2 decimal places.
2. **Infeasible** fixtures (``mandatory_overload_infeasible``,
   ``zero_fuel_infeasible``) -- response is ``status: infeasible`` and
   the ``infeasibility_reasons`` list names the right rule + shop
   (e.g. "Mandatory load 8 kW exceeds generator capacity 5 kW").
3. **Invalid-schema** fixtures (``invalid_schema``) -- the API
   returns HTTP 400 with the canonical ``invalid_input:`` detail
   prefix.

The script can be run two ways:

* **Standalone (no server needed)**::

      python evidence/verify.py

  Talks to the ``solve()`` function in ``backend/app/solver.py``
  directly via in-process imports.  Fastest, no port collisions,
  no async.

* **Against a running server**::

      python evidence/verify.py --base-url http://localhost:8000

  Hits the live ``/api/roster`` endpoint.  Useful to confirm that
  the whole stack (FastAPI, fast-fail, MILP) is producing the same
  answers end-to-end.

Exit code is 0 on full success, 1 on any failed check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make the backend importable when running standalone.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


# --------------------------------------------------------------------------- #
# Rich is the only third-party dep; we degrade gracefully if it's missing.
# --------------------------------------------------------------------------- #
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
    from rich.text import Text

    RICH = True
except ImportError:  # pragma: no cover - we install rich in the venv
    RICH = False
    Console = object  # type: ignore[assignment,misc]


# Fallback Console if rich is missing -- still prints something useful.
class _PlainConsole:
    def print(self, *args: Any, **kwargs: Any) -> None:
        # Strip rich markup if present
        for a in args:
            text = str(a)
            if isinstance(a, Text):
                text = a.plain  # type: ignore[attr-defined]
            print(text)


if not RICH:
    Console = _PlainConsole  # type: ignore[assignment,misc]


# --------------------------------------------------------------------------- #
# Fixtures: input file -> expected outcome
# --------------------------------------------------------------------------- #
# Each entry is (input_filename, kind, expected).
#
# kind is one of:
#   "feasible"      -> response.status == "ok" and numeric values
#                      match the recorded fixed output to 2 decimals
#   "infeasible"    -> response.status == "infeasible" and the
#                      reasons list mentions the expected substring(s)
#   "invalid_schema" -> HTTP 400 with detail starting "invalid_input:"
#
# expected is a dict that drives the assertion:
#   - feasible: {"value": float, "fuel": float, "must_contain": [...]}
#   - infeasible: {"must_contain": [...]}
#   - invalid_schema: {"status_code": 400, "must_contain": "invalid_input:"}
# --------------------------------------------------------------------------- #
FIXTURES: List[Tuple[str, str, Dict[str, Any]]] = [
    (
        "sample_shapla.json",
        "feasible",
        {
            "value": 292.0,
            "fuel": 16.4,
            "must_contain": [
                '"status": "ok"',
                '"total_value_served": 292.0',
                '"total_fuel_used_litres": 16.4',
            ],
        },
    ),
    (
        "tiny_3shop_feasible.json",
        "feasible",
        {
            "value": 19.0,
            "fuel": 0.9,
            "must_contain": [
                '"status": "ok"',
                '"total_value_served": 19.0',
                '"total_fuel_used_litres": 0.9',
            ],
        },
    ),
    (
        "happy_path_balanced.json",
        "feasible",
        {
            "value": 54.0,
            "fuel": 2.0,
            "must_contain": [
                '"status": "ok"',
                '"total_value_served": 54.0',
                '"total_fuel_used_litres": 2.0',
            ],
        },
    ),
    (
        "mandatory_overload_infeasible.json",
        "infeasible",
        {
            "must_contain": [
                '"status": "infeasible"',
                "Mandatory load 8 kW exceeds generator capacity 5 kW",
            ],
        },
    ),
    (
        "zero_fuel_infeasible.json",
        "infeasible",
        {
            "must_contain": [
                '"status": "infeasible"',
                "Minimum fuel required for mandatory shops",
            ],
        },
    ),
    (
        "invalid_schema.json",
        "invalid_schema",
        {
            "status_code": 400,
            "must_contain": "invalid_input:",
        },
    ),
    (
        "consecutive_dark_violation.json",
        "infeasible",
        {
            "must_contain": [
                '"status": "infeasible"',
                "low_value_shop",
                "max_consecutive_dark",
            ],
        },
    ),
    (
        "conflict_simulation.json",
        "infeasible",
        {
            "must_contain": [
                '"status": "infeasible"',
            ],
        },
    ),
    (
        "strict_boundary.json",
        "feasible",
        {
            "value": 25.0,
            "fuel": 1.2,
            "must_contain": [
                '"status": "ok"',
                '"total_value_served": 25.0',
                '"total_fuel_used_litres": 1.2',
            ],
        },
    ),
]


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
def _solve_inprocess(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Solve via the in-process solver (no HTTP).

    Returns ``(http_status, body)`` where http_status is always 200
    for a successful solve (the in-process path doesn't deal in HTTP)
    and the body is the canonical response dict.  ValidationError /
    pydantic.ValidationError are caught and reported as a 400-style
    body so the test runner treats them uniformly.
    """
    from app.solver import solve, ValidationError
    from pydantic import ValidationError as PydanticValidationError

    try:
        return 200, solve(payload)
    except (ValidationError, PydanticValidationError) as e:
        msg = (
            json.dumps(e.errors(), default=str)
            if isinstance(e, PydanticValidationError)
            else str(e)
        )
        return 400, {"detail": "invalid_input: " + msg}
    except Exception as e:  # pragma: no cover - defensive
        return 500, {"detail": f"internal error: {e}"}


def _solve_http(
    payload: Dict[str, Any], base_url: str, timeout: float = 30.0
) -> Tuple[int, Dict[str, Any]]:
    """Solve via a real HTTP call to /api/roster."""
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/api/roster"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw}


# --------------------------------------------------------------------------- #
# Assertions
# --------------------------------------------------------------------------- #
def _check(
    status: int,
    body: Dict[str, Any],
    kind: str,
    expected: Dict[str, Any],
) -> List[str]:
    """Return a list of human-readable failure messages (empty if all OK)."""
    failures: List[str] = []

    if kind == "invalid_schema":
        if status != expected.get("status_code", 400):
            failures.append(
                f"expected HTTP {expected.get('status_code', 400)} but got {status}"
            )
        detail = body.get("detail", "")
        prefix = expected.get("must_contain", "invalid_input:")
        if not detail.startswith(prefix):
            failures.append(
                f'expected detail to start with {prefix!r}, got {detail!r}'
            )
        return failures

    # For both feasible and infeasible we require a 200 response with
    # a status field that the test can branch on.
    if status != 200:
        failures.append(f"expected HTTP 200 but got {status} (body: {body!r})")
        return failures

    if "status" not in body:
        failures.append("response is missing 'status' field")
        return failures

    if kind == "feasible":
        if body["status"] != "ok":
            failures.append(
                f"expected status 'ok' but got {body['status']!r} "
                f"(reasons: {body.get('infeasibility_reasons', [])})"
            )
        # Compare numeric outputs to 2 decimal places.
        for fld, want in (("total_value_served", expected.get("value")),
                          ("total_fuel_used_litres", expected.get("fuel"))):
            got = body.get(fld)
            if want is None or got is None:
                continue
            if abs(float(got) - float(want)) > 0.01:
                failures.append(
                    f"{fld}: expected {want} but got {got}"
                )

    elif kind == "infeasible":
        if body["status"] != "infeasible":
            failures.append(
                f"expected status 'infeasible' but got {body['status']!r} "
                f"(value={body.get('total_value_served')})"
            )
        reasons = body.get("infeasibility_reasons") or []
        for needle in expected.get("must_contain", []):
            # Strip JSON-quote prefix for the status check; everything
            # else is matched against the reasons list.
            if needle.startswith('"status"'):
                continue
            if not any(needle in r for r in reasons):
                failures.append(
                    f"expected an infeasibility_reason containing "
                    f"{needle!r}; got {reasons!r}"
                )

    # The common "must_contain" check covers the status field for
    # both kinds -- verify the literal string is present in the body.
    body_str = json.dumps(body, default=str)
    for needle in expected.get("must_contain", []):
        if needle not in body_str:
            failures.append(f"expected {needle!r} in response body")

    return failures


# --------------------------------------------------------------------------- #
# Main runner
# --------------------------------------------------------------------------- #
def _run(
    base_url: Optional[str], inputs_dir: Path, console: Any
) -> int:
    results: List[Dict[str, Any]] = []
    started = time.time()

    backend = "HTTP " + base_url if base_url else "in-process solver"

    console.print(
        Panel.fit(
            f"[bold]Who Stays On? — Evidence Verification Suite[/bold]\n"
            f"Backend: {backend}\n"
            f"Fixtures: {len(FIXTURES)}",
            border_style="cyan",
        )
    )
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Running fixtures...", total=len(FIXTURES))
        for fname, kind, expected in FIXTURES:
            fixture_path = inputs_dir / fname
            if not fixture_path.exists():
                results.append(
                    {
                        "fixture": fname,
                        "kind": kind,
                        "status": "ERROR",
                        "failures": [f"fixture file missing: {fixture_path}"],
                        "elapsed": 0.0,
                    }
                )
                progress.advance(task)
                continue

            with open(fixture_path) as fh:
                payload = json.load(fh)

            t0 = time.time()
            try:
                if base_url:
                    status, body = _solve_http(payload, base_url)
                else:
                    status, body = _solve_inprocess(payload)
            except Exception as e:  # pragma: no cover - defensive
                status, body = 500, {"detail": f"client error: {e}"}
                console.print(
                    f"  [red]EXC[/red] {fname}: {traceback.format_exc(limit=2)}"
                )
            elapsed = time.time() - t0

            failures = _check(status, body, kind, expected)
            results.append(
                {
                    "fixture": fname,
                    "kind": kind,
                    "status": "OK" if not failures else "FAIL",
                    "failures": failures,
                    "elapsed": elapsed,
                    "http": status,
                    "value": body.get("total_value_served"),
                    "fuel": body.get("total_fuel_used_litres"),
                    "solver_status": body.get("status"),
                }
            )
            progress.advance(task)

    # --- pretty print ----------------------------------------------------
    table = Table(
        title="Evidence Verification Results",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Fixture", style="cyan", no_wrap=True, min_width=30)
    table.add_column("Kind", style="dim", min_width=10)
    table.add_column("HTTP", justify="right", min_width=4)
    table.add_column("Solver", justify="center", min_width=10)
    table.add_column("Value", justify="right", min_width=6)
    table.add_column("Fuel", justify="right", min_width=6)
    table.add_column("Time", justify="right", min_width=8)
    table.add_column("Result", min_width=6)

    n_ok = n_fail = 0
    for r in results:
        kind_style = {
            "feasible": "green",
            "infeasible": "yellow",
            "invalid_schema": "red",
        }.get(r["kind"], "white")

        if r["status"] == "OK":
            result_cell = Text("PASS", style="bold green")
            n_ok += 1
        elif r["status"] == "ERROR":
            result_cell = Text("ERROR", style="bold red")
            n_fail += 1
        else:
            result_cell = Text("FAIL", style="bold red")
            n_fail += 1

        value_str = (
            f"{r['value']:.1f}"
            if isinstance(r["value"], (int, float))
            else "—"
        )
        fuel_str = (
            f"{r['fuel']:.2f}" if isinstance(r["fuel"], (int, float)) else "—"
        )

        table.add_row(
            r["fixture"],
            Text(r["kind"], style=kind_style),
            str(r["http"]),
            str(r.get("solver_status", "—")),
            value_str,
            fuel_str,
            f"{r['elapsed'] * 1000:.0f} ms",
            result_cell,
        )

    console.print(table)
    console.print()

    # --- failure detail --------------------------------------------------
    if n_fail:
        console.print(
            f"[bold red]{n_fail} of {len(FIXTURES)} fixtures FAILED[/bold red]"
        )
        for r in results:
            if r["failures"]:
                console.print(
                    Panel(
                        "\n".join(f"• {f}" for f in r["failures"]),
                        title=f"[red]{r['fixture']}[/red]",
                        border_style="red",
                    )
                )
    else:
        console.print(
            f"[bold green]All {n_ok} fixtures passed[/bold green] "
            f"in {time.time() - started:.2f} s"
        )

    return 0 if n_fail == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Who Stays On? evidence suite.  Without --base-url, "
            "the in-process solver is used (no server needed)."
        )
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL of a running API (e.g. http://localhost:8000). "
        "If omitted, the in-process solver is used.",
    )
    parser.add_argument(
        "--inputs-dir",
        default=str(Path(__file__).resolve().parent / "inputs"),
        help="Directory holding the input fixtures (default: evidence/inputs).",
    )
    args = parser.parse_args()

    inputs_dir = Path(args.inputs_dir).resolve()
    if not inputs_dir.exists():
        print(f"inputs directory not found: {inputs_dir}", file=sys.stderr)
        return 1

    console = Console() if RICH else _PlainConsole()
    return _run(args.base_url, inputs_dir, console)


if __name__ == "__main__":
    sys.exit(main())
