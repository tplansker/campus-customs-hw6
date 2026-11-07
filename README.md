# Campus Customs — Vendor Desk Agent (HW6)

An AI agent that reads one vendor document (HTML, PDF, or `.eml`), pulls out the key facts
(vendor, SKU, unit price, lead time, minimum order), and recommends **recommend** or
**do_not_recommend** by the rules in the Campus Customs shop handbook — with three layers of
defense against booby-trapped documents.

Every AI call uses **`gpt-6-luna`** routed through **Portkey**.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then put your real PORTKEY_API_KEY in .env
```

`.env` is gitignored and must never be committed. Fill in `PORTKEY_API_KEY`; keep `OPENAI_MODEL=gpt-6-luna`.

## Run the agent on one document

```bash
python agent.py --doc data/practice_docs/p01_bluehen_quote.html
```

All three defense layers are on by default. Useful flags:

```bash
python agent.py --doc <path> --no-defenses        # the undefended Problem 3 agent
python agent.py --doc <path> --layers code,prompt  # pick specific layers: code,design,prompt
python agent.py --doc <path> --no-cache            # defeat prompt caching (Problem 4)
```

Each run appends to `output/usage_log.json` (tokens + cost), `output/audit_trail.json` (every tool
call + final answer), and — only if a document tricks the agent into the email/payment tools —
`output/trap_calls.json` (it starts as `[]` and should stay empty).

## The three defense layers

- **Code** (`sanitize.py`) — hands the model only the text a person would see; strips hidden text
  (CSS `display:none`/0-px/white, off-page or tiny PDF text, invisible characters).
- **Design** (`policy.py` + `tools.py`) — `read_document` is locked to the assigned file; `send_email`
  and `approve_payment` do nothing without a human sign-off (and log the attempt); the decision and
  reason are recomputed from the handbook in Python, so a document can't argue its way to a buy.
- **Prompt** (`prompts/prompt.md`) — security rules telling the model to treat the document as data,
  ignore "revisions"/"corrections" that contradict the main quote, and never use the dangerous tools.

## Reproduce the homework outputs

```bash
python cache_test.py      # Problem 4: with/without caching  -> output/cache_test.{json,md}
python attack_test.py     # Problem 5: undefended vs attacks  -> output/attack_results.{json,md}
python defense_test.py    # Problem 6: all layer configs      -> output/defense_results.{json,md}
python make_attacks.py    # Problem 7: build the two attacks  -> data/my_attacks/
python red_team_test.py   # Problem 7: run them per config    -> output/red_team_results.json
python exam_run.py        # Problem 8: exam docs              -> output/exam_results.json
```

Open `output/hw6_report.html` (Problem 9) by double-clicking — it is one self-contained file and
needs no internet.

## Layout

| Path | What it is |
|---|---|
| `agent.py`, `tools.py`, `models.py`, `prompts/prompt.md` | the agent |
| `sanitize.py`, `policy.py` | the code and design defense layers |
| `data/` | shop handbook, prices, practice key, and all practice / exam / attack documents |
| `output/` | every generated report and log |
| `AI_prompts.md` | log of the prompts typed for each problem |
