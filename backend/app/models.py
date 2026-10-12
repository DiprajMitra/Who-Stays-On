"""Pydantic v2 schemas for the /api/roster input.

These models double as:

* runtime validation for incoming requests (used by /api/roster and the
  /api/roster/dryrun endpoint), and
* auto-generated OpenAPI docs at ``/docs`` for judges / consumers.

They mirror the shape the solver already accepts (see ``solver._validate``)
so we do not duplicate any business rules here; this is structural only.

Strict-mode policy
------------------
All models are configured with ``model_config = ConfigDict(strict=True)``
*and* every numeric field is declared with ``Field(strict=True)``.  This
prevents the well-known Pydantic v2 coercions:

* ``True`` / ``False`` being accepted where a number is expected
* ``"20"`` (string) being accepted where a number is expected
* ``1.0`` (float) being accepted where an ``int`` is expected

A small ``_no_nan`` field-validator additionally rejects ``NaN`` and
``+/-Infinity`` on every float field; these are real JSON numbers and
Pydantic would otherwise happily round-trip them, which would silently
break the MILP (``math.isnan`` checks, comparisons, ``round`` rounding,
etc.).
"""

from __future__ import annotations

import math
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _no_nan(v: float) -> float:
    """Reject NaN / +/-Infinity on any float field.

    Returns the value unchanged so the field keeps its float type.  In
    strict mode, a string or bool would have already been rejected by
    Pydantic before this validator runs.
    """
    if math.isnan(v) or math.isinf(v):
        raise ValueError("must be a finite number (NaN and Infinity are not allowed)")
    return v


# --------------------------------------------------------------------------- #
# Primitive rows
# --------------------------------------------------------------------------- #
class FuelTier(BaseModel):
    """One step in the generator's fuel-consumption curve."""

    model_config = ConfigDict(strict=True)

    load_kw: float = Field(
        ..., gt=0, strict=True, description="Generator load threshold in kW"
    )
    litres_per_hour: float = Field(
        ...,
        ge=0,
        strict=True,
        description="Fuel consumption in L/h when this tier is active",
    )

    @field_validator("load_kw", "litres_per_hour")
    @classmethod
    def _finite(cls, v: float) -> float:
        return _no_nan(v)


class Shop(BaseModel):
    """A single shop in the complex."""

    model_config = ConfigDict(strict=True)

    id: str = Field(..., min_length=1, strict=True, description="Unique shop identifier")
    kw: float = Field(..., gt=0, strict=True, description="Power draw in kW when running")
    type: Literal["mandatory", "normal"] = Field(
        ..., description="mandatory shops must be powered during every outage hour"
    )
    value_per_slot: List[float] = Field(
        ...,
        min_length=1,
        description="Value served if powered in each slot (one entry per hour)",
    )

    @field_validator("value_per_slot")
    @classmethod
    def _no_negative_values(cls, v: List[float]) -> List[float]:
        for i, x in enumerate(v):
            if not isinstance(x, (int, float)) or isinstance(x, bool):
                raise ValueError(
                    f"value_per_slot[{i}] must be a number (got {type(x).__name__})"
                )
            if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
                raise ValueError(
                    f"value_per_slot[{i}] must be a finite number (NaN / Infinity not allowed)"
                )
            if x < 0:
                raise ValueError("value_per_slot entries must be non-negative")
        return [float(x) for x in v]


# --------------------------------------------------------------------------- #
# Top-level request
# --------------------------------------------------------------------------- #
class RosterRequest(BaseModel):
    """Full payload accepted by ``POST /api/roster``."""

    model_config = ConfigDict(strict=True)

    grid_available: List[bool] = Field(
        ..., min_length=1, description="True for each hour the grid is up"
    )
    generator_capacity_kw: float = Field(..., gt=0, strict=True)
    fuel_budget_litres: float = Field(..., ge=0, strict=True)
    fuel_table: List[FuelTier] = Field(..., min_length=1)
    max_consecutive_dark: int = Field(..., ge=0, strict=True)
    shops: List[Shop] = Field(...)

    @field_validator("generator_capacity_kw", "fuel_budget_litres")
    @classmethod
    def _finite_floats(cls, v: float) -> float:
        return _no_nan(v)

    @field_validator("grid_available")
    @classmethod
    def _booleans_only(cls, v: List[bool]) -> List[bool]:
        # In strict mode Pydantic already rejects non-bool entries, but we
        # double-check defensively in case the model is ever relaxed.
        for i, g in enumerate(v):
            if not isinstance(g, bool):
                raise ValueError(f"grid_available[{i}] must be a boolean")
        return v

    # ---- cross-field checks (cheap, just sanity) ---------------------------
    @field_validator("shops")
    @classmethod
    def _ids_unique(cls, v: List[Shop]) -> List[Shop]:
        ids = [s.id for s in v]
        if len(set(ids)) != len(ids):
            raise ValueError("shop ids must be unique")
        return v

    def n_hours(self) -> int:
        return len(self.grid_available)
