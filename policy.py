"""DESIGN defense layer, part 1: the decision is made by code, not by the model.

The model only extracts facts. This module reads the handbook's own tables (approved vendors, removed and
pending vendors, product catalogue) and applies the four purchasing checks from section 4 in plain Python.
Whatever a document says, it cannot argue its way past these checks, and the reason sentence is written by
code, so no link or instruction from a document can end up in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from models import VendorDecision

HANDBOOK = Path(__file__).resolve().parent / "data" / "shop_handbook.md"
MAX_LEAD_DAYS = 21
MAX_MIN_ORDER = 600
SKU_RE = re.compile(r"^CC-[A-Z]{2}-\d{3}$")
URLISH = re.compile(r"https?://|www\.|@|<|>|\]\(", re.I)


def _norm(name: str | None) -> str:
    """Compare vendor names ignoring case, punctuation, and trailing company words."""
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    s = re.sub(r"\b(inc|llc|ltd|co|company|corp)\b", " ", s)
    return " ".join(s.split())


@dataclass(frozen=True)
class Handbook:
    approved: dict[str, str]           # normalized name -> display name
    blocked: dict[str, str]            # normalized name -> status text
    max_cost: dict[str, float]         # SKU -> max unit cost
    item_names: dict[str, str]         # SKU -> item name


@cache
def handbook() -> Handbook:
    text = HANDBOOK.read_text(encoding="utf-8")
    sec5 = text.split("## 5.")[1].split("## 6.")[0]
    sec6 = text.split("## 6.")[1].split("## 7.")[0]
    sec9 = text.split("## 9.")[1].split("## 10.")[0]
    approved, blocked, max_cost, items = {}, {}, {}, {}
    for row in re.findall(r"^\|\s*[A-Z]{3}\s*\|\s*([^|]+?)\s*\|", sec5, re.M):
        approved[_norm(row)] = row
    for name, status in re.findall(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$", sec6, re.M):
        if name not in ("Vendor",) and not set(name) <= {"-"}:
            blocked[_norm(name)] = status
    for sku, item, cost in re.findall(r"^\|\s*(CC-[A-Z]{2}-\d{3})\s*\|\s*([^|]+?)\s*\|[^|]*\|[^|]*\|\s*\$([\d.]+)\s*\|", sec9, re.M):
        max_cost[sku] = float(cost)
        items[sku] = item
    return Handbook(approved, blocked, max_cost, items)


def _money(x: float) -> str:
    return f"${x:,.2f}"


def enforce(answer: VendorDecision) -> tuple[VendorDecision, dict]:
    """Recompute decision + reason from the extracted facts. Returns the enforced answer and a note."""
    hb = handbook()
    vendor = answer.vendor if answer.vendor and not URLISH.search(answer.vendor) else None
    sku = (answer.sku or "").strip().upper() or None
    if sku and not SKU_RE.match(sku):
        sku = None
    v = _norm(vendor)

    def fail(msg: str) -> tuple[str, str]:
        return "do_not_recommend", msg

    if not vendor:
        decision, reason = fail("Approved vendor check failed: the document does not name a vendor.")
    elif v in hb.blocked:
        decision, reason = fail(f"Approved vendor check failed: {vendor} is on the removed/pending list "
                                f"({hb.blocked[v].split('.')[0]}).")
    elif v not in hb.approved:
        decision, reason = fail(f"Approved vendor check failed: {vendor} is not on the approved vendor list.")
    elif sku is None or sku not in hb.max_cost:
        decision, reason = fail(f"Price check failed: SKU {sku or 'unknown'} is not in the product catalogue, "
                                "so there is no max unit cost to compare against.")
    elif answer.unit_price is None:
        decision, reason = fail("Price check failed: the document does not state a unit price.")
    elif answer.unit_price > hb.max_cost[sku]:
        decision, reason = fail(f"Price check failed: {_money(answer.unit_price)} is above the "
                                f"{_money(hb.max_cost[sku])} max unit cost for {sku}.")
    elif answer.lead_time_days is None:
        decision, reason = fail("Lead time check failed: the document does not state a lead time.")
    elif answer.lead_time_days > MAX_LEAD_DAYS:
        decision, reason = fail(f"Lead time check failed: {answer.lead_time_days} days is over the "
                                f"{MAX_LEAD_DAYS}-day limit.")
    elif answer.min_order is None:
        decision, reason = fail("Minimum order check failed: the document does not state a minimum order.")
    elif answer.min_order > MAX_MIN_ORDER:
        decision, reason = fail(f"Minimum order check failed: {answer.min_order} units is over the "
                                f"{MAX_MIN_ORDER}-unit limit.")
    else:
        decision = "recommend"
        reason = (f"All four checks pass: {vendor} is approved, {_money(answer.unit_price)} is at or below the "
                  f"{_money(hb.max_cost[sku])} max for {sku}, {answer.lead_time_days}-day lead time, "
                  f"{answer.min_order}-unit minimum.")

    enforced = answer.model_copy(update={"vendor": vendor, "sku": sku, "decision": decision, "reason": reason})
    note = {
        "model_decision": answer.decision,
        "model_reason": answer.reason,
        "policy_decision": decision,
        "overridden": answer.decision != decision,
    }
    return enforced, note
