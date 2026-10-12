"""Pydantic v2 schemas for the /api/roster input.

These models double as:

* runtime validation for incoming requests (used by /api/roster and the
  /api/roster/dryrun endpoint), and
* auto-generated OpenAPI docs at ``/docs`` for judges / consumers.

They mirror the shape the solver already accepts (see ``solver._validate``)
so we do not duplicate any business rules here; this is structural only.
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Primitive rows
# --------------------------------------------------------------------------- #
class FuelTier(BaseModel):
    """One step in the generator's fuel-consumption curve."""

    load_kw: float = Field(..., gt=0, description="Generator load threshold in kW")
    litres_per_hour: float = Field(
        ..., ge=0, description="Fuel consumption in L/h when this tier is active"
    )


class Shop(BaseModel):
    """A single shop in the complex."""

    id: str = Field(..., min_length=1, description="Unique shop identifier")
    kw: float = Field(..., gt=0, description="Power draw in kW when running")
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
        if any(x < 0 for x in v):
            raise ValueError("value_per_slot entries must be non-negative")
        return v


# --------------------------------------------------------------------------- #
# Top-level request
# --------------------------------------------------------------------------- #
class RosterRequest(BaseModel):
    """Full payload accepted by ``POST /api/roster``."""

    grid_available: List[bool] = Field(
        ..., min_length=1, description="True for each hour the grid is up"
    )
    generator_capacity_kw: float = Field(..., gt=0)
    fuel_budget_litres: float = Field(..., ge=0)
    fuel_table: List[FuelTier] = Field(..., min_length=1)
    max_consecutive_dark: int = Field(..., ge=0)
    shops: List[Shop] = Field(..., min_length=1)

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
