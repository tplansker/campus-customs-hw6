"""Problem 8: run the final defended agent (code + design + prompt layers) on every exam document.

    python exam_run.py

Writes output/exam_results.json straight from the agent's output and the API's token counts.
Nothing in that file is edited by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import agent
from attack_test import load as load_json_list

ROOT = Path(__file__).resolve().parent
DOCS = sorted((ROOT / "data" / "exam_docs").iterdir())
TRAPS = ROOT / "output" / "trap_calls.json"
OUT = ROOT / "output" / "exam_results.json"


def main() -> None:
    results = []
    for doc in DOCS:
        traps_before = len(load_json_list(TRAPS))
        answer, calls = agent.run(doc.relative_to(ROOT).as_posix(), cache=True, layers=agent.ALL_LAYERS)
        new_traps = load_json_list(TRAPS)[traps_before:]
        a = answer.model_dump()
        input_tokens = sum(c["input_tokens"] or 0 for c in calls)
        results.append({
            "file": doc.name,
            "vendor": a["vendor"],
            "sku": a["sku"],
            "unit_price": a["unit_price"],
            "lead_time_days": a["lead_time_days"],
            "min_order": a["min_order"],
            "decision": a["decision"],
            "reason": a["reason"],
            "trap_tool_called": bool(new_traps),
            "input_tokens_called": input_tokens > 0,   # the API reported input-token counts for this run
            "input_tokens": input_tokens,
            "cached_tokens": sum(c["cached_input_tokens"] or 0 for c in calls),
            "output_tokens": sum(c["output_tokens"] or 0 for c in calls),
            "cost_usd": round(sum(c["cost"]["total_usd"] for c in calls), 8),
        })
        r = results[-1]
        print(f"{doc.name:24} {r['decision']:17} trap={r['trap_tool_called']} ${r['cost_usd']:.6f} | {r['reason']}", flush=True)
    OUT.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(results)} entries)")


if __name__ == "__main__":
    main()
