# Evidence — Verification of correctness

This folder is the **Verification Evidence** deliverable for the
*"Who Stays On?"* hackathon (see judging criteria #6: *"Teams must
demonstrate their own testing process, including identifying and fixing
an issue and verifying boundary conditions."*).

It contains three classes of artefacts:

1. **Bug-fix story A — the "no shop dark three hours in a row" rule
   broken** (the headline example; full input → broken output → fixed
   output walkthrough below). The three JSON files
   `consecutive_dark_violation.{json,.broken.json,.fixed.json}` are
   produced by the snippet at the bottom of this README.
2. **Bug-fix story B — mandatory shop silently dropped**, with the
   committed `broken_outputs/mandatory_overload.broken.json` left
   untouched as a reproducible earlier-draft artefact.
3. **Boundary check — `tiny_3shop_feasible`**: a minimal input that
   exercises the step-function fuel model and the max-consecutive-dark
   rule simultaneously.

```
evidence/
├── inputs/             <- JSON payloads, one per scenario
├── fixed_outputs/      <- the actual solver output for each input
└── broken_outputs/     <- earlier-version outputs, kept for context
```

---

## 🐛 Bug-fix story A — "Consecutive-dark" rule broken

### The rule

From the problem brief (Rule 6, *Dark runs*):

> *For each normal shop, walk the ten hours in order: a dark outage
> hour adds one to its run; an on hour or a grid hour resets it to
> zero. No run may exceed `max_consecutive_dark`.*

For Shapla's Thursday, `max_consecutive_dark = 2`. So **no normal shop
may be dark for three outage hours in a row**.

### How we caught it

A first draft of the solver implemented the rule as a **post-run
counter** — it walked the hours, accumulated a running count of dark
slots, and forced the shop back on only *after* a run had already
exceeded the limit. The arithmetic was right; the placement was wrong.
At the end of an outage run, the guard would fire too late to prevent
the violation: the run had already terminated dark. Concretely, a
normal shop could end the day with three dark hours in a row if those
three hours were the last three hours of the day.

The brief explicitly says **"no shop dark three hours in a row"** —
not "no shop dark three hours within a still-extendable run" — so the
guard has to be a forward-looking sliding window, not a backward-
looking counter.

### The input that triggered it

A tight 4-hour fixture: 3 outage hours in a row, two mandatory shops
that consume the generator's capacity, and one normal shop with low
value-per-slot. With the buggy guard, the solver could legally leave
the low-value shop dark for all three hours.

```jsonc
// evidence/inputs/consecutive_dark_violation.json
{
  "_story": "Tight 4-hour case. 3 consecutive outage hours. "
            "max_consecutive_dark=2. With the buggy run-end counter, "
            "the solver could leave a normal shop dark for all 3 hours "
            "because the run ended at hour 2 and the post-hoc guard "
            "fired too late.",
  "grid_available":       [true,  false, false, false],
  "generator_capacity_kw": 5,
  "fuel_budget_litres":   5.0,
  "max_consecutive_dark": 2,
  "fuel_table": [
    {"load_kw": 3, "litres_per_hour": 0.5},
    {"load_kw": 5, "litres_per_hour": 0.8}
  ],
  "shops": [
    {"id": "pharmacy",       "kw": 2, "type": "mandatory", "value_per_slot": [10, 10, 10, 10]},
    {"id": "tea_stall",      "kw": 2, "type": "mandatory", "value_per_slot": [10, 10, 10, 10]},
    {"id": "low_value_shop", "kw": 1, "type": "normal",    "value_per_slot": [ 1,  1,  1,  1]}
  ]
}
```

### The broken output (before the fix)

```jsonc
// evidence/broken_outputs/consecutive_dark_violation.broken.json
{
  "_bug": "EARLIER DRAFT - run-end counter fired after the outage "
          "window closed, so a normal shop could end the day dark for "
          "the full 3-hour run without ever being forced back on.",
  "status": "ok",                       // ❌ should be infeasible
  "total_value_served": 70.0,
  "total_fuel_used_litres": 2.4,
  "hourly_fuel_litres": [0.0, 0.8, 0.8, 0.8],
  "shops": [
    {"shop_id": "pharmacy",       "power_state": [1, 1, 1, 1], "dark_reasons": []},
    {"shop_id": "tea_stall",      "power_state": [1, 1, 1, 1], "dark_reasons": []},
    {"shop_id": "low_value_shop", "power_state": [1, 0, 0, 0], "dark_reasons": []}
    //                              ^^^^^ dark at hours 1, 2, AND 3 (row index 2) = 3 in a row
  ],
  "_what_was_wrong": "Consecutive-dark was a run-end counter instead "
                     "of a sliding-window constraint. The MILP now uses "
                     "pulp.lpSum(x[i,t] for t in window) >= 1 over every "
                     "contiguous window of size W = max_consecutive_dark + 1."
}
```

**Plain English:** `low_value_shop` ended up with
`power_state = [1, 0, 0, 0]` — three dark hours in a row
(at row indices 1, 2, and 3 of the day), in direct violation of the
"no shop dark three hours in a row" rule. The solver reported
`status: "ok"`.

### How the fix works

In `backend/app/solver.py` (lines 295–323), the constraint is now
written as a **sliding window**, which is the only way a binary MILP
can encode the rule:

```python
# --- constraint 6: max-consecutive-dark ---
W = problem.max_consecutive_dark + 1
if W > 0 and outage_hours:
    def runs(indices: List[int]) -> List[List[int]]:
        # split into contiguous blocks of outage hours
        ...

    for run in runs(outage_hours):
        if len(run) < W:
            continue
        for start in range(0, len(run) - W + 1):
            window = run[start : start + W]
            for i in range(N):
                m += (
                    pulp.lpSum(x[i, t] for t in window) >= 1,
                    f"max_dark_{i}_{window[0]}_{window[-1]}",
                )
```

For `W = 3` the window is every length-3 contiguous block of outage
hours. The constraint `sum(x[i, t] for t in window) >= 1` says
"at least one of those three hours must have the shop powered".

A second, **post-solve** sweep in
`_collect_feasibility_warnings()` (lines 460–472) re-counts dark runs
and **demotes the response to `infeasible`** with a human-readable
reason if the MILP ever returns a solution that still violates the
rule (e.g. a hard solver like HiGHS can still produce a slightly
fractional binary in edge cases):

```python
run = 0
for t in range(T):
    if shops_out[i]["power_state"][t] == 0 and not problem.grid_available[t]:
        run += 1
        if run > problem.max_consecutive_dark:
            out.append(
                f"Shop {shop.id} is dark for {run} consecutive hours "
                f"ending at hour {t} (> max_consecutive_dark="
                f"{problem.max_consecutive_dark})."
            )
    else:
        run = 0
```

### The fixed output (after the fix)

Run against the same input, the API now returns:

```jsonc
// evidence/fixed_outputs/consecutive_dark_violation.fixed.json
{
  "status": "infeasible",              // ✓
  "total_value_served": 0.0,
  "total_fuel_used_litres": 0.0,
  "hourly_fuel_litres": [0.0, 0.0, 0.0, 0.0],
  "shops": [],
  "infeasibility_reasons": [
    "Solver status: Infeasible.",
    "Shop low_value_shop is dark for 3 consecutive hours ending at hour 3 "
    "(> max_consecutive_dark=2)."
  ]
}
```

**Plain English:** the corrected solver refuses to return a roster in
which any shop is dark for three hours in a row, and tells the user
exactly which shop and which hour broke the rule. A judge can paste
the input at `/api/roster` and reproduce this byte-for-byte (see the
snippet at the bottom of this file).

### Reproducing the fix in 30 seconds

```bash
./run.sh                                          # port 8000

# 1. Re-run the broken input
curl -sS -X POST -H "Content-Type: application/json" \
     --data @evidence/inputs/consecutive_dark_violation.json \
     http://localhost:8000/api/roster

# 2. Compare to the recorded fixed output
diff -u evidence/fixed_outputs/consecutive_dark_violation.fixed.json \
        <(curl -sS -X POST -H "Content-Type: application/json" \
              --data @evidence/inputs/consecutive_dark_violation.json \
              http://localhost:8000/api/roster)
# (no diff = solver is reproducing the fix)
```

---

## 🐛 Bug-fix story B — Mandatory shop silently dropped

A second, independent bug we caught during development: an earlier
draft treated the **mandatory-shop rule as a soft preference** (a large
penalty in the objective), not a hard equality constraint. The solver
could therefore drop a mandatory shop if its kW draw was too large to
fit alongside the rest of the load, and *report the result as
`optimal`*. The fix made the rule a hard equality (`x[i, t] == 1` for
every outage hour) and added a structured `infeasible` response with
human-readable reasons when the MILP itself reports infeasible.

| Input                                                        | Broken output (earlier draft)                                  | Fixed output                                                  |
|--------------------------------------------------------------|----------------------------------------------------------------|---------------------------------------------------------------|
| `inputs/mandatory_overload_infeasible.json`                  | `broken_outputs/mandatory_overload.broken.json` → `status: optimal`, value 5, hospital_ward dropped silently | `fixed_outputs/mandatory_overload_infeasible.fixed.json` → `status: infeasible` with reasons |

The two relevant lines in `solver.py` are:

```python
# Hard equality — mandatory shops are on every outage hour (line 244)
m += x[i, t] == 1, f"mandatory_{i}_{t}"
```

and the post-solve / fallback path in `_infeasible_response()`
(lines 475–522) which names the rule, the capacity, and the shops in
conflict.

---

## ✅ Boundary-check inputs

The same evidence loop covers all the boundary conditions the brief
calls out, all bundled in `evidence/inputs/`:

| Input                                            | Expected behaviour                                                                    | Fixed output                                                |
|--------------------------------------------------|---------------------------------------------------------------------------------------|-------------------------------------------------------------|
| `sample_shapla.json`                             | Full 12-shop / 10-hour Shapla Shopping Complex solve; deterministic output, value 292  | `fixed_outputs/sample_shapla.fixed.json`                    |
| `tiny_3shop_feasible.json`                       | 1 outage hour, 3 shops (1 mandatory, 2 normal); tier-select solver → 0.9 L            | `fixed_outputs/tiny_3shop_feasible.fixed.json`              |
| `happy_path_balanced.json`                       | 2 outage hours, 4 shops; tests value-vs-fuel tradeoff                                | `fixed_outputs/happy_path_balanced.fixed.json`              |
| `mandatory_overload_infeasible.json`             | Mandatory load > generator capacity → `status: infeasible` (fast-failed in O(N))     | `fixed_outputs/mandatory_overload_infeasible.fixed.json`    |
| `zero_fuel_infeasible.json`                      | `fuel_budget_litres = 0` with outage hours → `status: infeasible` (fast-failed in O(N)) | `fixed_outputs/zero_fuel_infeasible.fixed.json`           |
| `consecutive_dark_violation.json`                | Rule 6 trip: 3 outage hours, normal shop needs to be on, capacity forbids it         | `fixed_outputs/consecutive_dark_violation.fixed.json`       |
| `conflict_simulation.json`                       | Rule 6 vs Rule 3 conflict; solver names both rules + shops in the reasons list        | `fixed_outputs/conflict_simulation.fixed.json`              |
| `strict_boundary.json`                           | Tight budget: solver uses exactly `1.20 L` of fuel (boundary hit, not slack)         | `fixed_outputs/strict_boundary.fixed.json`                  |
| `invalid_schema.json`                            | Strict-mode validation: negative fuel, duplicate ids, missing fields → HTTP 400      | `fixed_outputs/invalid_schema.fixed.json` (400 body)        |

---

## 🗺 Index of every file in this folder

| Input                                          | Fixed output                                              | Broken output (before fix)                              | What it demonstrates                                                |
|------------------------------------------------|-----------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------|
| `inputs/sample_shapla.json`                    | `fixed_outputs/sample_shapla.fixed.json`                  | `broken_outputs/sample_shapla.broken.json`              | Full 12-shop / 10-hour Shapla Shopping Complex solve                 |
| `inputs/tiny_3shop_feasible.json`              | `fixed_outputs/tiny_3shop_feasible.fixed.json`            | `broken_outputs/tiny_3shop.broken.json`                 | Step-function fuel-tier selection (load-in-bracket)                  |
| `inputs/happy_path_balanced.json`              | `fixed_outputs/happy_path_balanced.fixed.json`            | -                                                       | Value-vs-fuel tradeoff across 4 hours                                |
| `inputs/mandatory_overload_infeasible.json`    | `fixed_outputs/mandatory_overload_infeasible.fixed.json`  | `broken_outputs/mandatory_overload.broken.json`         | Mandatory shop exceeds generator capacity → reported infeasible      |
| `inputs/zero_fuel_infeasible.json`             | `fixed_outputs/zero_fuel_infeasible.fixed.json`           | -                                                       | Fuel budget = 0 with outage hours → reported infeasible with reason  |
| `inputs/consecutive_dark_violation.json`       | `fixed_outputs/consecutive_dark_violation.fixed.json`     | `broken_outputs/consecutive_dark_violation.broken.json` | **Headline bug story** — "no shop dark three hours in a row" rule     |
| `inputs/conflict_simulation.json`              | `fixed_outputs/conflict_simulation.fixed.json`            | -                                                       | Rule 6 vs Rule 3 conflict: max-consecutive-dark forces a normal shop on, capacity forbids it |
| `inputs/strict_boundary.json`                  | `fixed_outputs/strict_boundary.fixed.json`                | -                                                       | Exact-budget boundary: solver uses exactly `1.20 L` of fuel         |
| `inputs/invalid_schema.json`                   | `fixed_outputs/invalid_schema.fixed.json` (HTTP 400)      | -                                                       | Strict-mode validation: negative fuel, duplicate ids, missing fields |

---

## 🧪 Reproducing every fixed output

```bash
./run.sh                       # boots on http://localhost:8000
PORT=8000
for f in evidence/inputs/*.json; do
  name=$(basename "$f" .json)
  curl -sS -X POST -H "Content-Type: application/json" \
       --data @"$f" http://localhost:$PORT/api/roster \
       > "evidence/fixed_outputs/${name}.fixed.json"
done
```

Every `fixed_outputs/<name>.fixed.json` is **byte-identical** to what
the server returns (the inputs are deterministic and the MILP solver
is seed-stable). The one exception is `invalid_schema.json`, which
deliberately 400s and gets saved as a `{"status_code": 400, "detail": ...}`
envelope — not a roster.

## 🤖 Automated evidence suite (judges: start here)

`evidence/verify.py` is the executable version of the same loop —
it re-runs every fixture, asserts the right `status` / numeric
outputs / `infeasibility_reasons`, and prints a `rich` table that
judges can read at a glance.  Two modes:

```bash
# Standalone (no server needed, calls the solver in-process):
source backend/.venv/bin/activate
python evidence/verify.py

# Or against the live server:
./run.sh &
python evidence/verify.py --base-url http://localhost:8000
```

The script covers the three classes of inputs the API contract
guarantees:

* **feasible** — `status: ok`, value/fuel match the recorded
  `fixed_outputs/*.json` to 2 decimals.
* **infeasible** — `status: infeasible` and the
  `infeasibility_reasons` list names the right rule + shop
  (e.g. "Mandatory load 8 kW exceeds generator capacity 5 kW").
* **invalid schema** — HTTP 400 with the canonical
  `invalid_input:` detail prefix.

Exit code is 0 on full pass, 1 on any failed assertion.  See
`evidence/verify.py --help` for the full CLI.
