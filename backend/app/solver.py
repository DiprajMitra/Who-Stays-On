"""
MILP solver for the 'Who Stays On?' power roster problem.

Decision variables
------------------
x[i, t] : binary, 1 if shop i is powered at hour t
g[t]    : binary, 1 if the generator is running at hour t
L[t]    : continuous, generator load (kW) at hour t (upper bound; >= actual demand)
z[k, t] : binary, 1 if the k-th (sorted) fuel tier is the active one at hour t

Objective
---------
Maximize total value served = sum over (i, t) of value_per_slot[i][t] * x[i, t].

The day is treated as a single 10-slot plan so the solver can look ahead and
trade off which shops to drop during low-value hours versus high-value ones.

Constraints
-----------
1. Grid-on hours  -> every shop powered for free.
2. Mandatory shops -> powered during every outage hour.
3. Generator capacity -> sum kw[i] * x[i,t] (over outage hours) <= capacity.
4. Fuel budget    -> sum_t fuel_t <= fuel_budget_litres.
5. Step-function tier selection
        - If the generator is running, exactly one tier is active.
        - Active tier k satisfies load_kw[k-1] < L[t] <= load_kw[k].
        - fuel[t] = sum_k (litres_per_hour[k] * z[k, t]).
6. Max-consecutive-dark
        For every shop i and every window of size (max_consecutive_dark+1),
        at least one slot in the window must be powered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import pulp


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #
@dataclass
class Shop:
    id: str
    kw: float
    type: str  # "mandatory" | "normal"
    value_per_slot: List[float]


@dataclass
class FuelTier:
    load_kw: float
    litres_per_hour: float


@dataclass
class Problem:
    grid_available: List[bool]
    generator_capacity_kw: float
    fuel_budget_litres: float
    fuel_table: List[FuelTier]
    max_consecutive_dark: int
    shops: List[Shop]

    @property
    def T(self) -> int:
        return len(self.grid_available)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
class ValidationError(ValueError):
    """Raised when the input payload is malformed."""


def _validate(payload: Dict[str, Any]) -> Problem:
    """Validate the incoming JSON payload and return a Problem object.

    Raises ValidationError on any structural or range problem.
    """
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a JSON object")

    # grid_available
    grid = payload.get("grid_available")
    if not isinstance(grid, list) or not grid:
        raise ValidationError("grid_available must be a non-empty list of booleans")
    if not all(isinstance(v, bool) for v in grid):
        raise ValidationError("grid_available entries must be booleans")

    T = len(grid)

    # generator capacity
    cap = payload.get("generator_capacity_kw")
    if not isinstance(cap, (int, float)) or cap <= 0:
        raise ValidationError("generator_capacity_kw must be a positive number")

    # fuel budget
    budget = payload.get("fuel_budget_litres")
    if not isinstance(budget, (int, float)) or budget < 0:
        raise ValidationError("fuel_budget_litres must be a non-negative number")

    # fuel table
    ft_raw = payload.get("fuel_table")
    if not isinstance(ft_raw, list) or not ft_raw:
        raise ValidationError("fuel_table must be a non-empty list of {load_kw, litres_per_hour}")
    tiers: List[FuelTier] = []
    for i, row in enumerate(ft_raw):
        if not isinstance(row, dict):
            raise ValidationError(f"fuel_table[{i}] must be an object")
        try:
            lk = float(row["load_kw"])
            lph = float(row["litres_per_hour"])
        except (KeyError, TypeError, ValueError):
            raise ValidationError(
                f"fuel_table[{i}] needs numeric load_kw and litres_per_hour"
            )
        if lk <= 0 or lph < 0:
            raise ValidationError(f"fuel_table[{i}] values must be non-negative and load_kw > 0")
        tiers.append(FuelTier(lk, lph))
    # sort by load_kw ascending
    tiers.sort(key=lambda t: t.load_kw)
    if any(t.load_kw > cap for t in tiers):
        raise ValidationError("every fuel_table load_kw must be <= generator_capacity_kw")
    if tiers[0].load_kw <= 0:
        raise ValidationError("smallest fuel_table load_kw must be > 0")

    # max_consecutive_dark
    mcd = payload.get("max_consecutive_dark")
    if not isinstance(mcd, int) or mcd < 0:
        raise ValidationError("max_consecutive_dark must be a non-negative integer")

    # shops
    sh_raw = payload.get("shops")
    if not isinstance(sh_raw, list) or not sh_raw:
        raise ValidationError("shops must be a non-empty list")
    seen_ids = set()
    shops: List[Shop] = []
    for i, s in enumerate(sh_raw):
        if not isinstance(s, dict):
            raise ValidationError(f"shops[{i}] must be an object")
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            raise ValidationError(f"shops[{i}].id must be a non-empty string")
        if sid in seen_ids:
            raise ValidationError(f"shops[{i}].id duplicates an earlier id: {sid!r}")
        seen_ids.add(sid)
        kw = s.get("kw")
        if not isinstance(kw, (int, float)) or kw <= 0:
            raise ValidationError(f"shops[{i}].kw must be a positive number")
        stype = s.get("type")
        if stype not in ("mandatory", "normal"):
            raise ValidationError(f"shops[{i}].type must be 'mandatory' or 'normal'")
        vps = s.get("value_per_slot")
        if not isinstance(vps, list) or len(vps) != T:
            raise ValidationError(
                f"shops[{i}].value_per_slot must be a list of length {T}"
            )
        if not all(isinstance(v, (int, float)) and v >= 0 for v in vps):
            raise ValidationError(
                f"shops[{i}].value_per_slot must contain non-negative numbers"
            )
        shops.append(Shop(id=sid, kw=float(kw), type=stype, value_per_slot=[float(v) for v in vps]))

    return Problem(
        grid_available=list(grid),
        generator_capacity_kw=float(cap),
        fuel_budget_litres=float(budget),
        fuel_table=tiers,
        max_consecutive_dark=int(mcd),
        shops=shops,
    )


# --------------------------------------------------------------------------- #
# PuLP 4 helpers
# --------------------------------------------------------------------------- #
def _bvar(problem: pulp.LpProblem, name: str) -> pulp.LpVariable:
    return pulp.LpVariable.fromDict(
        problem, {"name": name, "lowBound": 0, "upBound": 1, "cat": "Binary"}
    )


def _cvar(problem: pulp.LpProblem, name: str, lo: float = 0, hi: float | None = None) -> pulp.LpVariable:
    spec: Dict[str, Any] = {"name": name, "lowBound": lo, "cat": "Continuous"}
    if hi is not None:
        spec["upBound"] = hi
    return pulp.LpVariable.fromDict(problem, spec)


# --------------------------------------------------------------------------- #
# MILP solve
# --------------------------------------------------------------------------- #
def solve(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Solve the roster problem and return the response payload."""
    problem = _validate(payload)
    T = problem.T
    N = len(problem.shops)
    outage_hours = [t for t, on in enumerate(problem.grid_available) if not on]

    # --- model ---
    m = pulp.LpProblem("who_stays_on", pulp.LpMaximize)

    # x[i, t]
    x: Dict[Any, pulp.LpVariable] = {
        (i, t): _bvar(m, f"x_{i}_{t}") for i in range(N) for t in range(T)
    }
    # g[t]
    g: Dict[int, pulp.LpVariable] = {t: _bvar(m, f"g_{t}") for t in range(T)}
    # L[t] (continuous, only relevant during outage)
    L: Dict[int, pulp.LpVariable] = {
        t: _cvar(m, f"L_{t}", 0, problem.generator_capacity_kw) for t in range(T)
    }
    # z[k, t]
    K = len(problem.fuel_table)
    z: Dict[Any, pulp.LpVariable] = {
        (k, t): _bvar(m, f"z_{k}_{t}") for k in range(K) for t in range(T)
    }

    BIG_M = problem.generator_capacity_kw  # safe upper bound for big-M

    # --- objective: maximise value served ---
    m += pulp.lpSum(
        problem.shops[i].value_per_slot[t] * x[i, t]
        for i in range(N)
        for t in range(T)
    )

    # --- constraint 1: grid hours -> everything on ---
    for i in range(N):
        for t, on in enumerate(problem.grid_available):
            if on:
                m += x[i, t] == 1, f"grid_on_{i}_{t}"
                # grid hours: generator off
                m += g[t] == 0, f"gen_off_{t}"

    # --- constraint 2: mandatory shops during outage ---
    for i, s in enumerate(problem.shops):
        if s.type == "mandatory":
            for t in outage_hours:
                m += x[i, t] == 1, f"mandatory_{i}_{t}"

    # --- constraint 3: generator capacity during outage ---
    # Two halves: (a) load fits inside the capacity; (b) when the generator is
    # off (g[t] = 0) the load is zero too -- otherwise normal shops could stay
    # lit for free during outages, which is the bug we hit.
    for t in outage_hours:
        m += (
            pulp.lpSum(problem.shops[i].kw * x[i, t] for i in range(N))
            <= problem.generator_capacity_kw,
            f"capacity_{t}",
        )
        m += (
            pulp.lpSum(problem.shops[i].kw * x[i, t] for i in range(N))
            <= problem.generator_capacity_kw * g[t],
            f"capacity_uses_gen_{t}",
        )

    # --- constraint 4: tier selection & step-function fuel ---
    for t in outage_hours:
        # Load <= capacity * g[t] (no idle burn).
        m += L[t] <= problem.generator_capacity_kw * g[t], f"load_cap_{t}"
        # Exactly one tier active when g[t] == 1; none when g[t] == 0.
        m += pulp.lpSum(z[k, t] for k in range(K)) == g[t], f"one_tier_{t}"
        # Step-function: if tier k active, then load_kw[k-1] < L[t] <= load_kw[k]
        for k, tier in enumerate(problem.fuel_table):
            upper = tier.load_kw
            lower = problem.fuel_table[k - 1].load_kw if k > 0 else 0.0
            m += L[t] <= upper + BIG_M * (1 - z[k, t]), f"tier_upper_{k}_{t}"
            m += L[t] >= lower + 1e-3 - BIG_M * (1 - z[k, t]), f"tier_lower_{k}_{t}"

        # L[t] must be at least the actual demand (sum of powered shop kW)
        # so that fuel-tier selection is meaningful.  We use big-M tied to g[t].
        m += (
            L[t]
            >= pulp.lpSum(problem.shops[i].kw * x[i, t] for i in range(N))
            - BIG_M * (1 - g[t]),
            f"load_meets_demand_{t}",
        )

    # --- constraint 5: fuel budget ---
    m += (
        pulp.lpSum(
            problem.fuel_table[k].litres_per_hour * z[k, t]
            for k in range(K)
            for t in outage_hours
        )
        <= problem.fuel_budget_litres,
        "fuel_budget",
    )

    # --- constraint 6: max-consecutive-dark ---
    # For every window of size W = max_consecutive_dark + 1, at least one slot
    # must be on.  Restrict to outage hours: a shop can only be dark then.
    W = problem.max_consecutive_dark + 1
    if W > 0 and outage_hours:
        def runs(indices: List[int]) -> List[List[int]]:
            out: List[List[int]] = []
            cur: List[int] = []
            for t in indices:
                if cur and t == cur[-1] + 1:
                    cur.append(t)
                else:
                    if cur:
                        out.append(cur)
                    cur = [t]
            if cur:
                out.append(cur)
            return out

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

    # --- solve ---
    solver = pulp.HiGHS(mip=True, msg=False, timeLimit=8, gapRel=1e-4)
    stats = m.solve(solver=solver)
    if stats.status != pulp.LpSolveStatus.Optimal:
        return _infeasible_response(problem, stats.status_str)

    # --- extract results ---
    hourly_fuel: List[float] = [0.0] * T
    gen_used: Dict[int, int] = {}
    tier_used: Dict[int, FuelTier | None] = {}
    for t in range(T):
        if t in outage_hours and round(pulp.value(g[t]) or 0) == 1:
            gen_used[t] = 1
            chosen = None
            f_t = 0.0
            for k in range(K):
                if round(pulp.value(z[k, t]) or 0) == 1:
                    chosen = problem.fuel_table[k]
                    f_t += problem.fuel_table[k].litres_per_hour
            hourly_fuel[t] = float(f_t)
            tier_used[t] = chosen
    total_fuel = sum(hourly_fuel)

    shops_out: List[Dict[str, Any]] = []
    infeasibility_reasons: List[str] = []
    total_value = 0.0

    for i, s in enumerate(problem.shops):
        power_state: List[int] = [
            int(round(pulp.value(x[i, t]) or 0)) for t in range(T)
        ]

        # textual reasons for every dark outage hour
        reasons: List[str] = []
        for t in outage_hours:
            if power_state[t] == 0:
                reasons.append(_explain_dark(s, t, problem, gen_used, tier_used))

        # total value served for this shop
        shop_value = sum(s.value_per_slot[t] * power_state[t] for t in range(T))
        total_value += shop_value

        shops_out.append(
            {
                "shop_id": s.id,
                "power_state": power_state,
                "total_value_served": float(shop_value),
                "dark_reasons": reasons,
            }
        )

    # defensive feasibility checks -> collected as `infeasibility_reasons`
    _collect_feasibility_warnings(
        problem, shops_out, hourly_fuel, infeasibility_reasons
    )

    response_status = "infeasible" if infeasibility_reasons else "ok"
    return {
        "status": response_status,
        "total_value_served": float(total_value),
        "total_fuel_used_litres": float(total_fuel),
        "hourly_fuel_litres": hourly_fuel,
        "shops": shops_out,
        "infeasibility_reasons": infeasibility_reasons,
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _explain_dark(
    shop: Shop,
    t: int,
    problem: Problem,
    gen_used: Dict[int, int],
    tier_used: Dict[int, FuelTier | None],
) -> str:
    parts = [f"Hour {t}: {shop.id} is dark."]
    if problem.grid_available[t]:
        parts.append("grid is actually available here (unexpected).")
        return " ".join(parts)

    if shop.type == "mandatory":
        parts.append("This should never happen for a mandatory shop.")

    if not gen_used.get(t):
        parts.append(
            "Generator is idle this hour: fuel budget was better spent in "
            "other hours, or this hour's marginal value could not justify "
            "burning diesel."
        )
    else:
        tier = tier_used.get(t)
        if tier is None:
            parts.append("Generator running but no tier selected (model anomaly).")
        else:
            parts.append(
                f"Generator on tier {tier.load_kw:g} kW ({tier.litres_per_hour:g} L/h); "
                f"capacity was fully taken by higher-priority shops this hour."
            )
    return " ".join(parts)


def _collect_feasibility_warnings(
    problem: Problem,
    shops_out: List[Dict[str, Any]],
    hourly_fuel: List[float],
    out: List[str],
) -> None:
    T = problem.T
    total_fuel = sum(hourly_fuel)
    if total_fuel - 1e-6 > problem.fuel_budget_litres:
        out.append(
            f"Fuel budget violated: used {total_fuel:.3f} L > budget "
            f"{problem.fuel_budget_litres:.3f} L."
        )
    outage_hours = [t for t, on in enumerate(problem.grid_available) if not on]
    for t in outage_hours:
        load = sum(
            problem.shops[i].kw
            for i, s in enumerate(shops_out)
            if s["power_state"][t] == 1
        )
        if load - 1e-6 > problem.generator_capacity_kw:
            out.append(
                f"Hour {t}: load {load:g} kW exceeds generator capacity "
                f"{problem.generator_capacity_kw:g} kW."
            )
    for i, shop in enumerate(problem.shops):
        if shop.type == "mandatory":
            for t in outage_hours:
                if shops_out[i]["power_state"][t] == 0:
                    out.append(
                        f"Mandatory shop {shop.id} is dark at hour {t}."
                    )
    for i, shop in enumerate(problem.shops):
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


def _infeasible_response(problem: Problem, status: str) -> Dict[str, Any]:
    reasons: List[str] = [f"Solver status: {status}."]

    outage_hours = [t for t, on in enumerate(problem.grid_available) if not on]
    mand_kw = sum(s.kw for s in problem.shops if s.type == "mandatory")

    if outage_hours and mand_kw > problem.generator_capacity_kw:
        reasons.append(
            f"Mandatory load {mand_kw:g} kW exceeds generator capacity "
            f"{problem.generator_capacity_kw:g} kW."
        )

    if outage_hours and problem.fuel_table:
        needed_tier = next(
            (t for t in problem.fuel_table if t.load_kw >= mand_kw),
            None,
        )
        if needed_tier is None and mand_kw > 0:
            reasons.append(
                f"Generator capacity {problem.generator_capacity_kw:g} kW is "
                f"too small to power all mandatory shops ({mand_kw:g} kW)."
            )
        elif needed_tier is not None:
            min_fuel = needed_tier.litres_per_hour * len(outage_hours)
            if min_fuel - 1e-6 > problem.fuel_budget_litres:
                reasons.append(
                    f"Minimum fuel required for mandatory shops across "
                    f"{len(outage_hours)} outage hours is {min_fuel:.2f} L "
                    f"(budget {problem.fuel_budget_litres:.2f} L, tier "
                    f"{needed_tier.load_kw:g} kW)."
                )

    # max consecutive dark
    if problem.max_consecutive_dark == 0 and outage_hours:
        reasons.append(
            "max_consecutive_dark is 0 but there are outage hours -- every "
            "shop would need to be on every outage hour, which may be "
            "incompatible with capacity / fuel."
        )

    return {
        "status": "infeasible",
        "total_value_served": 0.0,
        "total_fuel_used_litres": 0.0,
        "hourly_fuel_litres": [0.0] * problem.T,
        "shops": [],
        "infeasibility_reasons": reasons,
    }
