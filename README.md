# Who Stays On?

A Mixed-Integer Linear Program (MILP) that decides which shops in a shopping
complex run on the diesel generator during today's load-shedding outage
windows. Built for the *"Roster Optimisation in Unreliable Grids"*
hackathon challenge.

> Given a list of shops (each with kW draw + value-per-hour), an outage
> schedule, generator capacity, fuel budget, and a fuel-tier table, find
> the schedule that **maximises total value** while honouring:
> - all "mandatory" shops during outages,
> - generator capacity per hour,
> - total fuel budget across the day,
> - max-consecutive-dark-hours per shop.

---

## ⚡ Single start command

```bash
./run.sh
```

That is the **only** command a judge needs. The script will:

1. Create a `backend/.venv` on first run (cached afterwards).
2. Install the four dependencies (FastAPI, Uvicorn, Pydantic v2, PuLP+HiGHS).
3. Boot the API and the bundled vanilla-JS frontend together.

> No internet at runtime. No Docker required (though `docker compose up --build` works equivalently).

### 🔌 Port

**The local port is `8000`.** It is hard-coded in `run.sh` and overridable
via `PORT=9000 ./run.sh` if needed. Once the server prints its banner,
point any browser at:

| URL                                 | What it serves                          |
|-------------------------------------|-----------------------------------------|
| `http://localhost:8000/`            | The roster UI (vanilla-JS frontend)     |
| `http://localhost:8000/api/roster`  | `POST` — the solver endpoint            |
| `http://localhost:8000/api/health`  | `GET` — liveness probe                  |
| `http://localhost:8000/docs`        | Interactive Swagger UI                  |

---

## 🎯 Where the decision logic lives (find it in <2 minutes)

**The entire MILP decision logic is in one file:**

### 📄 `backend/app/solver.py`

Open that file and you are looking at the solver. A quick map:

| Lines in `solver.py` | What is there                                          |
|---------------------|--------------------------------------------------------|
| 1–31                | Module docstring with full variable / constraint list  |
| 41–69               | `Shop`, `FuelTier`, `Problem` dataclasses              |
| 73–175              | `_validate()` — input validation (Pydantic-style)      |
| 197–389             | `solve()` — **the MILP itself** (objective + 6 constraints) |
| 395–425             | `_explain_dark()` — text reason for every dark cell    |
| 428–472             | `_collect_feasibility_warnings()` — defensive checks   |
| 475–522             | `_infeasible_response()` — structured 4xx-style reply  |

### 🚦 The 6 solver constraints (rules 3-6 in the problem brief)

In `solve()`, search for the `m +=` lines. Each constraint carries a
human-readable name (e.g. `mandatory_0_3`, `capacity_2`, `tier_upper_1_4`):

1. **Grid-on hours** — every shop powered for free; generator off.
2. **Mandatory shops** — forced on for every outage hour.
3. **Generator capacity** — `sum(kw × x[i,t]) ≤ capacity` and
   `sum(kw × x[i,t]) ≤ capacity × g[t]` (no "free power during outage").
4. **Step-function fuel tier** — binary `z[k,t]` selects exactly one tier
   per running hour, with big-M load-in-bracket constraints.
5. **Fuel budget** — `Σ litres_per_hour × z[k,t] ≤ fuel_budget_litres`.
6. **Max-consecutive-dark** — sliding window of size `max_consecutive_dark+1`
   must contain at least one `x[i,t] = 1`.

### 🔁 End-to-end request flow

```
Browser / curl  ──(POST /api/roster, JSON body)──▶
  FastAPI (backend/app/main.py)
    └─ Pydantic models (backend/app/models.py) — schema check
       └─ solver.solve(payload)  (backend/app/solver.py)  ◀── THE DECISION LOGIC
          └─ PuLP + HiGHS (offline, bundled in `highspy` wheel)
             └─ JSON response
                └─ static/app.js renders the roster board
```

If a judge only has two minutes, the path is:

```
run.sh  →  http://localhost:8000  →  POST /api/roster  →  backend/app/solver.py  →  solve()
```

---

## 🗂 Repo map

```
.
├── backend/                  Python package (FastAPI + PuLP/HiGHS)
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py           FastAPI app + endpoints
│   │   ├── solver.py         ★ MILP solver (PuLP) — the decision logic
│   │   └── models.py         Pydantic v2 input schema
│   └── requirements.txt
├── static/                   Vanilla-JS frontend (no CDN, fully offline)
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── evidence/                 Test inputs, broken outputs, fixed outputs
│   ├── inputs/               JSON payloads, one per scenario
│   ├── fixed_outputs/        Reproducible solver output
│   ├── broken_outputs/       Earlier buggy outputs, kept for context
│   └── README.md             ★ Bug-fix story (Verification Evidence)
├── sample_input.json         Canonical input (12-shop Shapla Complex)
├── run.sh                    ★ Single start command (port 8000)
├── Dockerfile                Single-service image, port 8000
├── docker-compose.yml        `docker compose up --build`
├── Who_Stays_On_Problem_Statement.pdf
└── Juding Criteria.pdf
```

---

## 🧪 Testing with the bundled evidence

After `./run.sh`:

```bash
for f in evidence/inputs/*.json; do
  curl -sS -X POST -H "Content-Type: application/json" \
       --data @"$f" http://localhost:8000/api/roster \
       > "evidence/fixed_outputs/$(basename "$f" .json).fixed.json"
done
```

Each regenerated file should be **byte-identical** to the one already in
`evidence/fixed_outputs/` — the solver is deterministic for a given input.
See **`evidence/README.md`** for the full input → expected output map
and the bug-fix story required by the "Verification Evidence" judging
criterion.

---

## 🛜 Offline-capable

- Frontend has **zero external dependencies** (no CDN, no Google Fonts).
- Solver uses **HiGHS** (open-source, bundled inside the `highspy` wheel
  — no external solver binary).
- The Docker image is fully self-contained; once pulled, no network is
  required to solve or to render the UI.

---

## 📦 Dependencies (pinned loosely)

| Package                  | Used for                                      |
|--------------------------|-----------------------------------------------|
| `fastapi>=0.110`         | HTTP framework, OpenAPI generation            |
| `uvicorn[standard]>=0.27`| ASGI server                                    |
| `pydantic>=2.0`          | Request validation, schema docs               |
| `pulp>=2.7`              | MILP modelling                                |
| `highspy>=1.5`           | Open-source solver PuLP binds to by default   |

---

## 🪪 License

MIT (academic / hackathon use).
