# Who Stays On?

A Mixed-Integer Linear Program that decides which shops in a shopping
complex run on the diesel generator during today's load-shedding outage
windows. Built for the *"Roster Optimisation in Unreliable Grids"*
hackathon challenge.

> Given a list of shops (each with kW draw + value-per-hour), an outage
> schedule, generator capacity, fuel budget, and a fuel-tier table, find
> the schedule that maximises total value while honouring:
> - all "mandatory" shops during outages,
> - generator capacity per hour,
> - total fuel budget across the day,
> - max-consecutive-dark-hours per shop.

## Quick start

### Single documented port: **8000**

| Method            | Command                          | URL                            |
|-------------------|----------------------------------|--------------------------------|
| Local (no Docker) | `./run.sh`                       | `http://localhost:8000/`       |
| Containerised     | `docker compose up --build`      | `http://localhost:8000/`       |
| Custom port       | `PORT=9000 ./run.sh`             | `http://localhost:9000/`       |

`./run.sh` creates a `backend/.venv` on first run, installs the four
dependencies (FastAPI, Uvicorn, Pydantic v2, PuLP+HiGHS), and serves both
the API and the bundled frontend on port 8000.

## URLs cheat-sheet

Once the server is running on port 8000:

| Path                  | Purpose                                                  |
|-----------------------|----------------------------------------------------------|
| `/`                   | The vanilla-JS frontend (loads `static/index.html`)      |
| `/static/...`         | CSS / JS assets bundled into the image                    |
| `/docs`               | Interactive Swagger UI for the API                       |
| `/openapi.json`       | Machine-readable OpenAPI 3 schema                        |
| `/api/health` (GET)   | `{"status":"ok",...}` liveness probe                     |
| `/api/roster` (POST)  | Solve a roster; returns full JSON or `status: infeasible` |
| `/api/roster/dryrun` (POST) | Validate the payload format without solving        |

## Repo map

```
.
├── backend/                  Python package (FastAPI + PuLP/HiGHS)
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py           FastAPI app + endpoints
│   │   ├── solver.py         MILP solver (PuLP)
│   │   └── models.py         Pydantic v2 input schema
│   └── requirements.txt
├── static/                   Vanilla-JS frontend (no CDN, fully offline)
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── evidence/                 Test inputs, broken outputs, fixed outputs
│   ├── inputs/
│   ├── fixed_outputs/
│   ├── broken_outputs/
│   └── README.md
├── sample_input.json         Canonical input (12-shop Shapla Complex)
├── run.sh                    Unified launcher, port 8000
├── Dockerfile                Single-service image, port 8000
├── docker-compose.yml        docker compose up --build
├── Who_Stays_On_Problem_Statement.pdf
└── Juding Criteria.pdf
```

## How a request flows

```
static/index.html  ──(paste JSON, click "Solve")──▶
  fetch POST /api/roster
        │
        ▼
  FastAPI  ── pydantic RosterRequest validates shape ──▶
        │
        ▼
  solver.solve(payload)  ── MILP (PuLP + HiGHS, fully offline) ──▶
        │
        ▼
  JSON {status, total_value_served, total_fuel_used_litres,
        hourly_fuel_litres, shops: [...], reasons?: [...]}
        │
        ▼
  static/app.js renders the kpis, the fuel-burn chart, and the
  Shop × Hour roster table (with hover-reason on dark cells).
```

## Testing with the bundled evidence

After `./run.sh`:

```bash
for f in evidence/inputs/*.json; do
  curl -sS -X POST -H "Content-Type: application/json" \
       --data @"$f" http://localhost:8000/api/roster \
       > "evidence/fixed_outputs/$(basename "$f" .json).fixed.json"
done
```

Each regenerated file should be byte-identical to the one already in
`evidence/fixed_outputs/` (the solver is deterministic for a given
input). See `evidence/README.md` for the full input → expected output
map.

## Offline-capable

- Frontend has **zero external dependencies** (no CDN, no Google Fonts).
- Solver uses **HiGHS** (open-source, bundled inside the `highspy` wheel
  — no external solver binary).
- The Docker image is fully self-contained; once pulled, no network is
  required to solve or to render the UI.

## Dependencies (pinned loosely)

| Package          | Used for                                      |
|------------------|-----------------------------------------------|
| `fastapi>=0.110` | HTTP framework, OpenAPI generation            |
| `uvicorn[standard]>=0.27` | ASGI server                          |
| `pydantic>=2.0`  | Request validation, schema docs               |
| `pulp>=2.7`      | MILP modelling                                 |
| `highspy>=1.5`   | Open-source solver PuLP binds to by default    |

## License

MIT (academic / hackathon use).
