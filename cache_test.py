"""Problem 4: run the agent on all 12 practice documents with and without prompt caching.

    python cache_test.py

with_cache:    instructions (prompt + handbook) are identical on every call, so later calls read the
               prefix from the provider's prompt cache.
without_cache: agent.build_agent(cache=False) puts a fresh random id at the START of the instructions on
               every call, so no call can match a cached prefix.

Every model call from both setups is saved to output/cache_test.json, and the script checks the
API's own token counts to prove each setup did what it claims.
"""

from __future__ import annotations

import json
from pathlib import Path

import agent
from tools import now_iso

ROOT = Path(__file__).resolve().parent
DOCS = sorted((ROOT / "data" / "practice_docs").iterdir())
KEY = {row["file"]: row for row in json.loads((ROOT / "data" / "practice_key.json").read_text())}
OUT = ROOT / "output" / "cache_test.json"
FIELDS = ("vendor", "sku", "unit_price", "lead_time_days", "min_order", "decision")


def run_setup(cache: bool) -> tuple[list[dict], list[dict]]:
    setup = "with_cache" if cache else "without_cache"
    calls, docs = [], []
    for doc in DOCS:
        rel = doc.relative_to(ROOT).as_posix()
        answer, per_call = agent.run(rel, cache=cache, layers=agent.NO_DEFENSES)
        for c in per_call:
            calls.append({"setup": setup, "document": doc.name, **c})
        key = KEY[doc.name]
        docs.append({
            "setup": setup,
            "document": doc.name,
            "decision": answer.decision,
            "key_decision": key["decision"],
            "all_fields_match_key": all(getattr(answer, f) == key[f] for f in FIELDS),
            "cost_usd": round(sum(c["cost"]["total_usd"] for c in per_call), 8),
        })
        print(f"{setup:14} {doc.name:32} {answer.decision:17} "
              f"cached={sum(c['cached_input_tokens'] or 0 for c in per_call):6} "
              f"${docs[-1]['cost_usd']:.6f}")
    return calls, docs


def totals(calls: list[dict], setup: str) -> dict:
    rows = [c for c in calls if c["setup"] == setup]
    s = lambda k: sum(c[k] or 0 for c in rows)  # noqa: E731
    return {
        "model_calls": len(rows),
        "documents": len({c["document"] for c in rows}),
        "input_tokens": s("input_tokens"),
        "cached_input_tokens": s("cached_input_tokens"),
        "cache_write_tokens": s("cache_write_tokens"),
        "uncached_input_tokens": s("uncached_input_tokens"),
        "output_tokens": s("output_tokens"),
        "reasoning_tokens": s("reasoning_tokens"),
        "calls_with_cached_reads": sum(1 for c in rows if (c["cached_input_tokens"] or 0) > 0),
        "cost_usd": round(sum(c["cost"]["total_usd"] for c in rows), 8),
        "cost_breakdown_usd": {
            k: round(sum(c["cost"][k] for c in rows), 8)
            for k in ("uncached_input", "cached_input", "cache_write", "output")
        },
    }


def main() -> None:
    calls_w, docs_w = run_setup(cache=True)
    calls_n, docs_n = run_setup(cache=False)
    calls, docs = calls_w + calls_n, docs_w + docs_n
    summary = {s: totals(calls, s) for s in ("with_cache", "without_cache")}

    # Proof checks, straight from the API's reported token counts.
    checks = {
        "without_cache_has_zero_cached_reads": summary["without_cache"]["cached_input_tokens"] == 0,
        "with_cache_reads_cache_on_most_calls": summary["with_cache"]["calls_with_cached_reads"]
        >= summary["with_cache"]["model_calls"] - 1,
    }
    OUT.write_text(json.dumps({
        "generated": now_iso(),
        "model": agent.MODEL_NAME,
        "prices": json.loads(agent.PRICES_FILE.read_text())[agent.MODEL_NAME],
        "checks": checks,
        "summary": summary,
        "documents": docs,
        "calls": calls,
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": checks, "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
