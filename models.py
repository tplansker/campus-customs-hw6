"""Data shapes for the vendor desk agent.

VendorDecision is the agent's structured output: PydanticAI validates the model's answer against it,
so every run returns the same seven fields that section 13 of the shop handbook asks for.
DeskDeps is the per-run context handed to every tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class VendorDecision(BaseModel):
    """What the vendor desk reports for one vendor document (handbook section 13)."""

    vendor: str | None = Field(
        description="Vendor name exactly as the document gives it, or null if the document does not say."
    )
    sku: str | None = Field(
        description="Campus Customs SKU, e.g. CC-HD-006. Null if it is not in the document and cannot be "
        "matched to a catalogue item by name."
    )
    unit_price: float | None = Field(
        description="Quoted price per unit in US dollars, as a number (21.80, not '$21.80'). Null if not stated."
    )
    lead_time_days: int | None = Field(
        description="Lead time as the number of days the document states. Null if not stated."
    )
    min_order: int | None = Field(
        description="Minimum order quantity in units. Null if not stated."
    )
    decision: Literal["recommend", "do_not_recommend"] = Field(
        description="recommend only if all four purchasing checks in handbook section 4 pass."
    )
    reason: str = Field(
        description="One sentence that names the purchasing check that decided the result."
    )


LAYERS = ("code", "design", "prompt")   # the three defense layers; any subset can be switched on


@dataclass
class DeskDeps:
    """Context for one agent run, shared with every tool through RunContext.deps."""

    doc_path: Path                     # the vendor document this run was asked to review
    project_root: Path                 # used to resolve relative paths and to find output/
    run_id: str                        # timestamp id that ties trap calls back to a run
    layers: frozenset[str] = frozenset()   # which defense layers are on for this run (empty = no defenses)
    human_signoff: bool = False        # design layer: only a person can set this; the model and documents never can
    notes: list[str] = field(default_factory=list)  # what the code layer stripped out, for the audit trail
