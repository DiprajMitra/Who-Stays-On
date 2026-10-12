# Mathematical Strategist — Formal MILP Brief for "Who Stays On?"

**Author:** Mathematical Strategist
**Audience:** Engineering team
**Goal:** Translate the text rules of *Who_Stays_On_Problem_Statement.pdf* and
the evaluation axes of *Judging Criteria.pdf* into a single, unambiguous
Mixed-Integer Linear Program (MILP) plus a deterministic reason-generation
post-process. The optimiser must **maximise total value served** without ever
violating capacity, fuel, mandatory, or max-consecutive-dark constraints.

---

## 1. Notation

| Symbol | Domain | Meaning |
|---|---|---|
| `T = 10` | constant | Number of hourly slots, indexed `t = 0..9` (= 10:00…19:00). |
| `N` | input | Number of shops, indexed `i = 0..N-1`. |
| `K` | input | Number of fuel-table tiers, indexed `k = 0..K-1`, sorted ascending by `load_kw`. The last tier must satisfy `load_kw[K-1] = generator_capacity_kw`. |
| `g[t] ∈ {0,1}` | variable | Generator on at hour `t` (0 = idle, 1 = running). |
| `x[i,t] ∈ {0,1}` | variable | Shop `i` is powered at hour `t`. |
| `L[t] ∈ ℝ₊` | variable | Continuous combined load (kW) used for tier lookup at hour `t`. |
| `z[k,t] ∈ {0,1}` | variable | Tier `k` is the *active* fuel tier at hour `t`. |
| `fuel[t] ∈ ℝ₊` | derived | Litres burned at hour `t` = Σₖ `lph[k] · z[k,t]`. |
| `demand[i,t] = kw[i] · x[i,t]` | derived | Power actually drawn. |
| `M = generator_capacity_kw` | constant | Big-M constant, safe upper bound on `L[t]`. |
| `ε = 1e-3` | constant | Tiny slack to enforce strict inequality on lower bound of `L[t]`. |
| `W = max_consecutive_dark + 1` | constant | Window length for the no-three-dark rule. |

Inputs are the validated JSON: `grid_available[10]`, `generator_capacity_kw`,
`fuel_budget_litres`, `fuel_table[].{load_kw, litres_per_hour}`,
`max_consecutive_dark`, `shops[].{id, kw, type, value_per_slot[10]}`.

---

## 2. Objective Function (Rule 7 — maximise value)

```
maximise   Σᵢ Σₜ  v[i,t] · x[i,t]
```

where `v[i,t] = shops[i].value_per_slot[t]`. The summation runs over **all**
10 hours (not just outage hours): grid-on hours naturally contribute their
fixed value because `x[i,t] = 1` is forced there (see §3.1). Equal-value
rosters are equally good — no tie-breaking required by the spec.

---

## 3. Hard Constraints

### 3.1 Grid hours — free power, generator off (Rule 1)

For every hour `t` where `grid_available[t] = true`:

```
x[i,t] = 1           for all i         (everyone powered)
g[t]   = 0                              (generator idle, no fuel)
```

*Implementation note:* these are equality constraints, not bounds. They
also force `L[t] = 0` indirectly via §3.3.

### 3.2 Mandatory shops during outages (Rule 5)

For every shop `i` with `type = "mandatory"` and every outage hour `t`:

```
x[i,t] = 1
```

If a shop is `mandatory` and `kw[i] > generator_capacity_kw` *or* the minimum
mandatory fuel exceeds `fuel_budget_litres`, the model is **infeasible by
construction** — the solver returns `infeasible` and we attach the
explanation defined in §6.

### 3.3 Generator capacity (Rule 3)

For every outage hour `t`:

```
Σᵢ kw[i] · x[i,t]   ≤  generator_capacity_kw         (C1)
Σᵢ kw[i] · x[i,t]   ≤  generator_capacity_kw · g[t]   (C2)
```

(C1) caps the powered load at the machine's nameplate. (C2) is the
*generator-usage* constraint: when the generator is idle (`g[t] = 0`) the
left side must be zero — without this, a normal shop could "stay on for
free" during an outage, which is the canonical bug the spec calls out.
Both halves are required.

### 3.4 Fuel-table step function (Rule 4)

The tier table is a **non-decreasing step function** of the *combined*
hourly load: pick the first tier whose `load_kw` is `≥` the load, charge
that tier's `litres_per_hour` for the whole hour, regardless of how many
shops are running. Per-shop consumption is **never** summed.

To model this with linear constraints, introduce a tier-selector
`z[k,t]` and a continuous load `L[t]`. For every outage hour `t`:

```
(a)  L[t] ≤ generator_capacity_kw · g[t]                          (load zero if gen off)
(b)  Σₖ z[k,t] = g[t]                                             (exactly one tier iff running)
(c)  L[t] ≥ Σᵢ kw[i] · x[i,t]  −  M·(1 − g[t])                    (L is an upper bound on demand)
(d)  For each tier k:
       L[t] ≤ load_kw[k]                  + M·(1 − z[k,t])        (upper side of bracket)
       L[t] ≥ load_kw[k-1] + ε           − M·(1 − z[k,t])        (strict lower side; for k=0, lower = 0)
```

Where `load_kw[-1] := 0` and `load_kw[K-1] = generator_capacity_kw`.

**Rounding rule (Rule 4 in plain English):** "find the first level that is
at least that much, charge its litres". Translate to MILP as: tier `k` is
active iff `load_kw[k-1] < L[t] ≤ load_kw[k]`. The `ε` slack turns the
strict `<` into `<=`, which the spec's "whole hundredths of a litre" rules
out as a meaningful difference at integer kW loads. If your table has
fractional kW breakpoints, scale to the smallest kW step and use
`ε = (smallest step)/2` instead.

`fuel[t]` is then `Σₖ lph[k] · z[k,t]`, and is the value displayed under
the board for that hour.

### 3.5 Daily fuel budget (Rule 4)

```
Σₜ fuel[t]   ≤   fuel_budget_litres
```

Summing across outage hours only is equivalent (grid hours have `fuel[t]=0`
by §3.1). All fuel arithmetic uses **hundredths of a litre** internally
(round after solve to 2 dp for output) — no tolerance on the *constraint*
itself, only on the post-solve display.

### 3.6 Max-consecutive-dark (Rule 6)

Walk hours `0..9` in order. A *dark run* for shop `i` is a maximal sequence
of consecutive outage hours with `x[i,t] = 0`. Grid hours and powered
hours reset the run to zero. The day starts at zero. The run length must
**never exceed** `max_consecutive_dark`.

**MILP encoding** — the standard "sliding window" trick: let
`W = max_consecutive_dark + 1`. For every shop `i` and every window of `W`
consecutive hours (over the 10-hour day, not just outage hours) that lies
*entirely inside a maximal outage run*:

```
Σ_{t in window}  x[i,t]  ≥  1
```

Restricting the windows to outage runs is a small optimisation: windows
that span a grid hour are already satisfied because the grid hour forces
`x[i,t] = 1`. Mandatory shops never enter this constraint (they are on
every outage hour by §3.2, so no window can ever be all-zero).

**Edge case — `max_consecutive_dark = 0`:** the model is only feasible if
every outage hour powers every normal shop that is below capacity and
within fuel. If not feasible, return `infeasible` with a message naming
`max_consecutive_dark` as the rule in conflict.

### 3.7 Feasibility envelope (Rule 9)

Before solving, run these cheap pre-checks and short-circuit to
`infeasible` if any fail:

1. `Σ mandatory_kw ≤ generator_capacity_kw` (else capacity rules them out).
2. `len(fuel_table) ≥ 1`, first `load_kw > 0`, last `load_kw == capacity`,
   strictly increasing, no negative values.
3. Minimum mandatory fuel `= lph[tier] * num_outage_hours` where `tier`
   is the first one with `load_kw ≥ mandatory_kw`. If that exceeds
   `fuel_budget_litres`, fuel rules them out.
4. For any mandatory shop, `kw[i] ≤ generator_capacity_kw`.

A model that passes these but is still infeasible at solve time is
reported as `infeasible` with a generic solver status; the four pre-checks
above are the only ones that produce a *naming-the-rule-and-shops*
explanation per the spec.

---

## 4. Variable Bounds Summary

```
0 ≤ x[i,t] ≤ 1        (binary)
0 ≤ g[t]   ≤ 1        (binary)
0 ≤ z[k,t] ≤ 1        (binary)
0 ≤ L[t]   ≤ generator_capacity_kw
```

---

## 5. Solution Recovery & Post-Processing

After the solver returns an optimal (or best-effort) solution:

1. `power_state[i,t] = round(x[i,t])`  (0 or 1).
2. `L[t] = max(Σᵢ kw[i]·power_state[i,t], 0)` — recompute deterministically
   from the integer `power_state`, do **not** trust the LP value of `L[t]`.
3. Pick the active tier by applying the spec's lookup rule directly:
   `tier[t] = first k with load_kw[k] ≥ L[t]` (zero load → tier "0 kW, 0 L/h").
4. `fuel[t] = tier[t].litres_per_hour` (or 0 if `L[t] = 0`).
5. `total_fuel_used_litres = round(Σₜ fuel[t], 2)`.
6. `total_value_served = Σᵢ Σₜ v[i,t] · power_state[i,t]`.
7. `per_shop_dark_hours = |{t outage : power_state[i,t] = 0}|` (mandatory
   shops are always 0 by construction).
8. Round all displayed kW/Litre numbers to whole hundredths (Rule 4).

A final defensive sweep recomputes (1)–(7) from the integer roster and
re-checks every constraint; any violation becomes an
`infeasibility_reasons` entry and the response status flips to
`infeasible`.

---

## 6. Reason Generation (the "why this shop is dark" text)

Every dark outage cell must carry a one-line explanation per Rule 8.
Reasons are generated in **this priority order** (first match wins), and
the explanatory text must include the **actual numbers** from the solve —
not a re-statement of the rule:

### 6.1 Hard-rule reasons (in this order)

1. **Mandatory** *(should never fire)* — `"Hour t: <id> is mandatory and should always be on — solver anomaly."`
2. **Generator off — fuel exhausted earlier**
   - Trigger: `g[t] = 0` *and* `Σ_{s<t, s outage} fuel[s]` is within `0.01 L` of `fuel_budget_litres`.
   - Text: `"Hour t: generator idle — would exceed 17 L total fuel budget (running total 16.98 L)."`
   - Always print the *real* budget and running total, not literal "17 L".
3. **Generator off — value too low to justify the next tier jump**
   - Trigger: `g[t] = 0` *and* at the smallest load-bearing tier,
     `lph[k] > 0` and the marginal value of any one droppable shop is
     `< lph[k] · value_of_one_litre_proxy` (cheap proxy: use
     `max v[i,t] / kw[i]` over droppable shops).
   - Text: `"Hour t: generator idle — no shop's hourly value justified the 0.80 L/h burn (best kw-value < burn cost)."`.
4. **Generator off — capacity not needed (low-demand hour)**
   - Trigger: `g[t] = 0` *and* total mandatory load at `t` is 0 (no
     outage) — actually impossible by Rule 1; this clause is a safety net.
5. **Generator on, but capacity full**
   - Trigger: `g[t] = 1` and `Σᵢ kw[i]·power_state[i,t] > 0.99·generator_capacity_kw`
     and this shop is one of the dropped ones.
   - Text: `"Hour t: would exceed 20 kW generator capacity (tier k=7 kW, current load 7 kW, this shop +3 kW = 10 kW)."`.
     Always include the tier's `load_kw` and the actual current load.
6. **Generator on, capacity fine, but fuel-cascade says no**
   - Trigger: `g[t] = 1` and dropping this shop saves exactly one tier
     worth of fuel at this hour.
   - Text: `"Hour t: keeping on would push to next fuel tier (2.0 L/h vs 1.2 L/h) and the marginal value didn't justify it."`.

### 6.2 Trade-off reasons (last resort)

If the shop is dark but the solver simply chose a different shop of higher
value at the same hour:

7. **Outranked by a higher-value shop**
   - Text: `"Hour t: outranked — <other_shop_id> (value <v>) at same load slot beat this shop's value <v2>."`
   - Use the *actual* comparison: pick the shop that the solver added in
     its place (same `x=1`, same hour) whose `kw` is closest to this
     shop's `kw` and whose `v` is higher.

### 6.3 The reason must be **defensible**

Rule 8 requires "the rule or trade-off, with numbers". The minimal
defensible reason is **one** of the six above, each carrying the real
kW, real L/h, real running-total, and the shop's own value. **Never**
return a generic "shop is dark" string — that is a validation failure.

---

## 7. The "Strong Entry" Guarantee (per the spec's closing note)

The spec warns that greedy per-hour "pick the most valuable shops" is
fragile: it dumps fuel at noon and starves the evening. The MILP
formulation above **cannot** make that mistake because:

- The objective sums value across **all** hours.
- The fuel budget is a **single** day-wide constraint.
- The step-function fuel is computed on the **combined** load per hour.

A greedy solver would have to look at all 10 hours jointly to be correct
anyway; at that point it is the same problem with a worse algorithm.
Maximise value with full look-ahead — the engineers' job is to feed the
solver the formulation in §3 verbatim and not to invent heuristic
shortcuts.

---

## 8. Implementation Checklist for Engineers

| # | Item | Section |
|---|---|---|
| 1 | Implement data classes and the validator from `schema.md` | §1 |
| 2 | Pre-checks in §3.7 *before* building the LP | §3.7 |
| 3 | Build the LP with variables in §4 and constraints §3.1–§3.6 | §3 |
| 4 | Solve with HiGHS (gap ≤ 1e-4, timeLimit ≤ 8 s) | §5 |
| 5 | Re-derive `fuel[t]` and `tier[t]` from the integer `power_state` | §5.2–§5.3 |
| 6 | Run defensive feasibility sweep; flip status to `infeasible` on violation | §5.8 |
| 7 | Generate `dark_reasons[i]` for every `(i, t outage)` with `power_state[i,t]=0`, in priority order §6.1 → §6.2 | §6 |
| 8 | Round displayed kW and litres to two decimals; keep internal precision | §5.8 |
| 9 | Return `HTTP 200 {status: ok, …}` or `{status: infeasible, …}`; `HTTP 400` for invalid input | Rule 8/9 |
| 10 | Honour the minute-150 update by re-running the whole pipeline on the new input | Rule 10 |

---

## 9. Worked Sanity Check (from the spec's 5-shop example)

Generator 10 kW, budget 8.00 L, table `{0–4:0.80, 4–7:1.20, 7–10:1.60}`.
Pharmacy (3 kW, mandatory) is on every outage hour.

- Hour 12:00, load = 3+1 = 4 kW (pharmacy + tea_stall, both on) → tier
  `4 kW, 0.80 L/h`. **No per-shop 0.20 L breakdown** — single combined
  charge, exactly as the spec says "once per hour on the combined load".
- Hour 13:00, load = 3+1+2 = 6 kW → still `4 kW tier, 0.80 L/h` (6 > 4 so
  next tier `7 kW, 1.20 L/h` is not reached — wait, the rule is "first
  level that is at least that much": for load 6, the first `load_kw ≥ 6`
  is 7, so `1.20 L/h` is correct). The spec table shows 13:00 as `10/1.60`
  in the worked example because pharmacy+tailor+mobile+tea_stall = 9 kW
  is also forced by the 3-hour rule on `mobile` — both readings are
  consistent; the spec is showing the *optimal* roster, not the smallest
  possible load. **Engineers: trust the solver output, not hand
  arithmetic.**

This is the test case in `sample_input.json` / the worked example —
verify your implementation against it before touching the 12-shop
production input.

---

## 10. Out-of-Scope (intentionally not modelled)

- Generator start-up cost, warm-up time, minimum run-time — not in the
  spec; adding them risks violating Rule 3 ("never exceed capacity in
  any hour").
- Per-shop fuel accounting — explicitly forbidden by Rule 4.
- Day-ahead forecasting of which hours will be outages — the spec
  treats the `grid_available` vector as known and fixed.
- A primary objective of "minimise dark hours" or "minimise fuel" —
  Rule 7 fixes the objective to value maximisation. The defensive
  sweep in §5.8 is the only place secondary metrics influence the
  response (by flipping status to `infeasible`).

If the engineering team wants to add tie-breakers for equal-value
rosters, do so **inside** the MILP with a tiny secondary objective
(e.g., `+ ε · Σᵢ Σₜ x[i,t]` for `ε = 1e-6`); do not post-process.
