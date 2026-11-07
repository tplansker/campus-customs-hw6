"""Campus Customs vendor desk agent.

Usage:
    python agent.py --doc data/practice_docs/p01_bluehen_quote.html                 # all defenses on
    python agent.py --doc data/practice_docs/p01_bluehen_quote.html --no-defenses   # Problem 3 agent
    python agent.py --doc ... --layers code,prompt                                  # pick layers

Defense layers (Problem 6), each can be switched on or off:
  code   - sanitize.py: the model only gets the text a person would see (hidden text is stripped).
  design - read_document is locked to the assigned file, send_email / approve_payment are blocked without
           a human sign-off, and policy.py recomputes the decision and reason from the handbook in code.
  prompt - the "Security rules" block in prompts/prompt.md is included in the instructions.

Reads one vendor document with the read_document tool and returns a VendorDecision. Every AI call goes to
gpt-6-luna through Portkey with PORTKEY_API_KEY. After each run it appends to output/usage_log.json
(token counts and dollar cost) and output/audit_trail.json (every tool call and the final answer).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import httpx2
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from models import LAYERS, DeskDeps, VendorDecision
from policy import enforce
from tools import TOOLS, append_json, ensure_json_list, now_iso

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
PROMPT_FILE = ROOT / "prompts" / "prompt.md"
HANDBOOK_FILE = ROOT / "data" / "shop_handbook.md"
PRICES_FILE = ROOT / "data" / "prices.json"

# Project .env first, then the course-wide one in the parent folder (load_dotenv never overrides).
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

REQUIRED_MODEL = "gpt-6-luna"
MODEL_NAME = os.getenv("OPENAI_MODEL", REQUIRED_MODEL).strip()
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")
ALL_LAYERS = frozenset(LAYERS)
NO_DEFENSES: frozenset[str] = frozenset()
SECURITY_BLOCK = re.compile(r"<!-- SECURITY RULES START.*?<!-- SECURITY RULES END -->", re.S)
# Portkey's gateway config can replay a stored response for an identical request (x-portkey-cache-status: HIT),
# returning the OLD token counts without calling the model. Force a real model call unless told otherwise,
# and record Portkey's cache status for every call so replays are visible in the logs.
PORTKEY_FORCE_REFRESH = os.getenv("PORTKEY_FORCE_REFRESH", "true").strip().lower() != "false"
GATEWAY_STATUS: list[str] = []


async def _record_gateway_status(response: Any) -> None:
    GATEWAY_STATUS.append(response.headers.get("x-portkey-cache-status", "n/a"))


RUN_LIMITS = UsageLimits(request_limit=8, tool_calls_limit=8, total_tokens_limit=100_000)


def build_model() -> OpenAIResponsesModel:
    """gpt-6-luna, routed through Portkey."""
    if MODEL_NAME != REQUIRED_MODEL:
        sys.exit(f"OPENAI_MODEL is '{MODEL_NAME}', but this assignment must use {REQUIRED_MODEL}.")
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    if not key:
        sys.exit("PORTKEY_API_KEY is not set. Add it to .env (see .env.example).")
    headers = {"x-portkey-api-key": key}
    if PORTKEY_FORCE_REFRESH:
        headers["x-portkey-cache-force-refresh"] = "true"
    client = AsyncOpenAI(base_url=PORTKEY_BASE_URL, api_key=key, default_headers=headers,
                         http_client=httpx2.AsyncClient(timeout=120, event_hooks={"response": [_record_gateway_status]}))
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def build_instructions(prompt_rules: bool = False) -> str:
    """Static system text: the task prompt, then the full handbook. Identical on every call.
    prompt_rules=False removes the Security rules block (the prompt defense layer)."""
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    prompt = SECURITY_BLOCK.sub("", prompt).strip() if not prompt_rules else prompt.strip()
    handbook = HANDBOOK_FILE.read_text(encoding="utf-8").strip()
    return f"{prompt}\n\n---\n\n{handbook}\n"


def build_agent(cache: bool = True, layers: frozenset[str] = ALL_LAYERS) -> Agent[DeskDeps, VendorDecision]:
    """cache=True: the instructions are byte-identical on every call, so the provider can serve the
    prompt + handbook prefix from its prompt cache.
    cache=False: a fresh random id goes at the very START of the instructions on every model call. Prompt
    caching only matches an identical prefix, so no call can ever reuse a cached prefix."""
    static = build_instructions(prompt_rules="prompt" in layers)
    if cache:
        instructions: Any = static
    else:
        def instructions() -> str:  # PydanticAI re-evaluates this before every model request
            return f"Cache-buster: {uuid.uuid4().hex}\n\n{static}"
    return Agent(
        build_model(),
        name="vendor_desk",
        deps_type=DeskDeps,
        output_type=VendorDecision,
        instructions=instructions,
        tools=TOOLS,
        model_settings={"max_tokens": 4_000},
    )


def dollar_cost(usage: Any) -> dict[str, Any]:
    """Cost of one run from the token counts the API reported, using data/prices.json."""
    rates = json.loads(PRICES_FILE.read_text(encoding="utf-8"))[MODEL_NAME]
    cached = usage.cache_read_tokens or 0
    written = usage.cache_write_tokens or 0
    uncached = max((usage.input_tokens or 0) - cached - written, 0)
    parts = {
        "uncached_input": uncached * rates["input_per_1m"] / 1e6,
        "cached_input": cached * rates["cached_input_per_1m"] / 1e6,
        "cache_write": written * rates["cache_write_per_1m"] / 1e6,
        "output": (usage.output_tokens or 0) * rates["output_per_1m"] / 1e6,
    }
    return {"total_usd": round(sum(parts.values()), 8), **{k: round(v, 8) for k, v in parts.items()}}


def _short(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def tool_calls_from(messages: list[Any]) -> list[dict[str, Any]]:
    """Pair every tool call in the conversation with a short version of its result."""
    calls: dict[str, dict[str, Any]] = {}
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = {"tool": part.tool_name, "args": part.args_as_dict(), "result": None}
            elif isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                calls[part.tool_call_id]["result"] = _short(part.content)
            elif isinstance(part, RetryPromptPart) and part.tool_call_id in calls:
                calls[part.tool_call_id]["result"] = "ERROR: " + _short(part.content)
    # The final_result call is PydanticAI's structured-output mechanism, not a real tool.
    return [c for c in calls.values() if not c["tool"].startswith("final_result")]


def model_calls_from(messages: list[Any]) -> list[dict[str, Any]]:
    """One record per model request, with every token count the API reported for it."""
    out = []
    for i, msg in enumerate(m for m in messages if isinstance(m, ModelResponse)):
        u = msg.usage
        out.append({
            "call": i + 1,
            "model": msg.model_name,
            "provider_response_id": msg.provider_response_id,
            "portkey_cache_status": GATEWAY_STATUS[i] if i < len(GATEWAY_STATUS) else None,
            "input_tokens": u.input_tokens,
            "cached_input_tokens": u.cache_read_tokens,
            "cache_write_tokens": u.cache_write_tokens,
            "uncached_input_tokens": max((u.input_tokens or 0) - (u.cache_read_tokens or 0) - (u.cache_write_tokens or 0), 0),
            "output_tokens": u.output_tokens,
            "reasoning_tokens": u.output_reasoning_tokens,
            "raw_details": dict(u.details or {}),
            "cost": dollar_cost(u),
        })
    return out


def run(doc: str, cache: bool = True, layers: frozenset[str] = ALL_LAYERS) -> tuple[VendorDecision, list[dict[str, Any]]]:
    """Run the agent on one document. Returns the answer and a usage record per model call.
    layers: which defense layers are on (ALL_LAYERS by default; NO_DEFENSES = the Problem 3 agent)."""
    layers = frozenset(layers)
    if unknown := layers - ALL_LAYERS:
        raise ValueError(f"Unknown defense layers: {sorted(unknown)}")
    for name in ("trap_calls.json", "usage_log.json", "audit_trail.json"):
        ensure_json_list(OUTPUT / name)

    doc_path = Path(doc)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    deps = DeskDeps(doc_path=doc_path, project_root=ROOT, run_id=run_id, layers=layers)
    prompt = f"Review this vendor document and report your decision: {doc_path.as_posix()}"

    setup = "with_cache" if cache else "without_cache"
    GATEWAY_STATUS.clear()
    result = build_agent(cache, layers).run_sync(prompt, deps=deps, usage_limits=RUN_LIMITS)
    usage = result.usage
    answer = result.output
    policy_note = None
    if "design" in layers:   # the decision and reason come from code, not from the model
        answer, policy_note = enforce(answer)
    messages = result.all_messages()
    model_name = next((m.model_name for m in messages if isinstance(m, ModelResponse) and m.model_name), MODEL_NAME)

    calls = model_calls_from(messages)
    append_json(OUTPUT / "usage_log.json", {
        "run_id": run_id,
        "setup": setup,
        "layers": sorted(layers),
        "document": doc_path.as_posix(),
        "time": now_iso(),
        "model": model_name,
        "requests": usage.requests,
        "portkey_cache_status": list(GATEWAY_STATUS),
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.output_reasoning_tokens,
        "total_tokens": (usage.input_tokens or 0) + (usage.output_tokens or 0),
        "cost": dollar_cost(usage),
    })
    append_json(OUTPUT / "audit_trail.json", {
        "run_id": run_id,
        "setup": setup,
        "document": doc_path.as_posix(),
        "time": now_iso(),
        "layers": sorted(layers),
        "tool_calls": tool_calls_from(messages),
        "hidden_text_removed": deps.notes,
        "policy_check": policy_note,
        "final_answer": answer.model_dump(),
    })
    return answer, calls


def main() -> None:
    parser = argparse.ArgumentParser(description="Campus Customs vendor desk agent")
    parser.add_argument("--doc", required=True, help="path to one vendor document (.html, .pdf, or .eml)")
    parser.add_argument("--no-cache", action="store_true", help="defeat prompt caching (for the cache test)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--no-defenses", action="store_true", help="run the undefended Problem 3 agent")
    group.add_argument("--layers", default=",".join(LAYERS), help="comma-separated defense layers: code,design,prompt")
    args = parser.parse_args()
    layers = NO_DEFENSES if args.no_defenses else frozenset(x for x in args.layers.split(",") if x.strip())
    if not (ROOT / args.doc).is_file() and not Path(args.doc).is_file():
        sys.exit(f"No such file: {args.doc}")
    answer, _ = run(args.doc, cache=not args.no_cache, layers=layers)
    print(json.dumps(answer.model_dump(), indent=2))


if __name__ == "__main__":
    main()
