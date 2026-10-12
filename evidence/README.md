# Evidence

This folder contains curated test fixtures that demonstrate the solver
behaves correctly across the four behaviours the problem statement calls
out: **feasible solve**, **infeasibility with reasons**, **mandatory-shop
rule**, and **fuel budget guardrail**.

```
evidence/
├── inputs/             <- JSON payloads, one per scenario
├── fixed_outputs/      <- the actual solver output for each input
└── broken_outputs/     <- the output of an EARLIER buggy version, kept for context
```

## Index

| Input                                          | Fixed output                                              | Broken output (before fix)                        | What it demonstrates                                                |
|------------------------------------------------|-----------------------------------------------------------|---------------------------------------------------|----------------------------------------------------------------------|
| `inputs/sample_shapla.json`                    | `fixed_outputs/sample_shapla.fixed.json`                  | `broken_outputs/sample_shapla.broken.json`        | Full 12-shop / 10-hour Shapla Shopping Complex solve                 |
| `inputs/tiny_3shop_feasible.json`              | `fixed_outputs/tiny_3shop_feasible.fixed.json`            | `broken_outputs/tiny_3shop.broken.json`           | Step-function fuel-tier selection (load-in-bracket)                  |
| `inputs/happy_path_balanced.json`              | `fixed_outputs/happy_path_balanced.fixed.json`            | -                                                 | Value-vs-fuel tradeoff across 4 hours                                |
| `inputs/mandatory_overload_infeasible.json`    | `fixed_outputs/mandatory_overload_infeasible.fixed.json`  | `broken_outputs/mandatory_overload.broken.json`  | Mandatory shop exceeds generator capacity → reported infeasible      |
| `inputs/zero_fuel_infeasible.json`             | `fixed_outputs/zero_fuel_infeasible.fixed.json`           | -                                                 | Fuel budget = 0 with outage hours → reported infeasible with reason  |

## Reproducing the fixed outputs

Start the server (single command from repo root):

```bash
./run.sh                       # boots on http://localhost:8000
```

Then in another shell:

```bash
PORT=8000
for f in evidence/inputs/*.json; do
  name=$(basename "$f" .json)
  curl -sS -X POST -H "Content-Type: application/json" \
       --data @"$f" http://localhost:$PORT/api/roster \
       > "evidence/fixed_outputs/${name}.fixed.json"
done
```

Each `fixed_outputs/<name>.fixed.json` is **byte-identical** to what the
server returns (the inputs are deterministic and the MILP solver is
seed-stable).

## Anatomy of a fixed output

For a feasible solve (`tiny_3shop_feasible.fixed.json`):

```json
{
  "status": "ok",
  "total_value_served": 19.0,
  "total_fuel_used_litres": 1.4,
  "hourly_fuel_litres": [1.4],
  "shops": [
    { "id": "pharmacy",  "power_state": [1], "...": "..." },
    { "id": "tea_stall", "power_state": [1], "...": "..." },
    { "id": "mobile_shop","power_state": [1], "...": "..." }
  ]
}
```

For an infeasible solve (`mandatory_overload_infeasible.fixed.json`):

```json
{
  "status": "infeasible",
  "infeasibility_reasons": [
    "Solver status: Infeasible.",
    "Mandatory load 8 kW exceeds generator capacity 5 kW.",
    "Generator capacity 5 kW is too small to power all mandatory shops (8 kW)."
  ],
  "shops": [],
  "hourly_fuel_litres": [0.0, 0.0]
}
```

## Anatomy of a broken output

Each `broken_outputs/*.broken.json` is a **synthetic** earlier-version
output kept to show what the bug used to produce. The `_bug` and
`_what_was_wrong` fields document the regression, and `_expected_fixed`
points at the now-correct value (or the full corrected object for
infeasible cases).
