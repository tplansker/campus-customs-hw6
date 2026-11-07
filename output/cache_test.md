# Cache Test — With vs. Without Cached Tokens

I ran all 12 practice documents through the agent in two setups (24 runs, 48 model calls). Every model call is saved in
`output/cache_test.json`. To rerun: `python cache_test.py`.

## How each setup works

- **with_cache:** The instructions (prompt + full handbook, about 10,800 tokens) are identical on every call and come first in the
  prompt. The provider can match that prefix and serve it from its prompt cache.
- **without_cache:** `build_agent(cache=False)` puts a new random line (`Cache-buster: <uuid>`) at the very
  start of the instructions before every model call. Prompt caching only matches an identical prefix, so a different first line
  means no call can reuse anything.

## Proof from the API's token counts

| Setup | Model calls | Calls with cached reads | Cached input tokens | Cache write tokens | Uncached input tokens | Output tokens |
|---|---|---|---|---|---|---|
| with_cache | 24 | **23 of 24** | **249,128** | 12,936 | 3,024 | 2,102 |
| without_cache | 24 | **0 of 24** | **0** | 262,634 | 3,024 | 2,094 |

- **without_cache really reused nothing:** `cached_tokens` is 0 on all 24 calls.
- **with_cache really reused the handbook:** only the first call (p01, call 1) missed. It wrote 10,842 tokens to the cache, and
  every call after that read about 10,840 cached tokens.
- **Writes can't be switched off.** Even without_cache reports cache *writes*. See the surprises section below.

## Cost

| | with_cache | without_cache |
|---|---|---|
| Total, 12 documents | **$0.00546** | **$0.03418** |
| Per document | **$0.000455** | **$0.002848** |
| Per document, steady state (p02–p12, cache already warm) | $0.000351 | $0.002848 |
| Breakdown: uncached input / cached input / cache write / output | $0.00030 / $0.00249 / $0.00162 / $0.00105 | $0.00030 / $0 / $0.03283 / $0.00105 |

**Savings: caching cut the bill by 84.0%** ($0.03418 → $0.00546). Measured once the cache is warm, it's 87.7% ($0.002848 → $0.000351 per document).

Caching changed the cost but not the answers. Both setups got the same 8 of 12 documents right. The same four (p02, p03,
p07, p12) were wrong in both setups, because each contains text that overrides the quote. That is the safety problem for later.

## Real numbers vs. the Problem 2 estimate

Problem 2 priced only the handbook (10,058 tokens) for **one call**.

| | Problem 2 estimate | Real, per document | Ratio |
|---|---|---|---|
| No caching | $0.0010058 per call | $0.002848 | 2.8× |
| Cached | $0.00010058 per call (cache hit) | $0.000351 (warm) / $0.000455 (avg) | 3.5× / 4.5× |
| 1,000 units, no caching | $1.0058 (1,000 calls) | $2.85 (1,000 docs) | 2.8× |
| 1,000 units, caching | $0.1017 (1 write + 999 hits) | $0.35–$0.46 (1,000 docs) | 3.5–4.5× |
| Savings from caching | ~90% | 84–88% | |

Why the real numbers are higher:

1. **Two model calls per document, not one.** Call 1 decides to use `read_document`. Call 2 reads the tool result and answers. Each
   call resends the full prompt, which doubles the input.
2. **Each call is ~11,070 tokens, not 10,058 (+10%).** On top of the handbook, each call carries `prompt.md`, the three tool schemas,
   the `VendorDecision` output schema, the user message, and (on call 2) the document text.
3. **Output tokens aren't free.** About 175 output tokens per document at $0.50/M (5× the input rate). That's 19% of the cached bill.
4. **"No caching" here is billed at the cache-write rate ($0.125/M), not the input rate ($0.10/M).** Explained in the surprises section.

Once you account for these, the real numbers match the arithmetic. For example, without cache: 2 calls × ~11,000 tokens × $0.125/M +
output ≈ $0.00285 per document.

## Projection: 1,000 documents a day for 30 days (30,000 documents)

| Setup | Cost per document | 30-day cost |
|---|---|---|
| without_cache (as measured, every token written to cache) | $0.002848 | **$85.45** |
| No cache at plain input price ($0.10/M, if writes weren't billed) | $0.002301 | $69.03 |
| with_cache, average from this test (one cold start per 12 documents) | $0.000455 | $13.65 |
| with_cache, steady state (warm cache + one fresh write per day) | $0.000351 | **$10.57** |

At this volume, caching saves about **$72–75 a month** (≈ 85–88%). The handbook could grow, or the volume could rise, and the cached
version would still stay cheap.

## Surprises, and why they happen

1. **Without cache cost more than "no caching" should.** This `gpt-6-luna` deployment (reached through Portkey on
   Azure, reported as `gpt-6-luna-global`) writes **every** prompt to a 24-hour cache. Asking for in-memory caching is rejected:
   *"This model is compatible only with 24h extended prompt caching."* `store=False` didn't stop it either. So each cache-busted call
   pays $0.125/M to write ~11,000 tokens that will never be read. That makes it 24% more expensive than plain uncached input. The cache-buster
   stops cache **reads** (which the test requires), but on this model you can't avoid paying for **writes**.
2. **Correction (found in Problem 6): the p01 and p08 with_cache calls were replays, not new calls.** I first wrote that the Problem 3
   cache "wasn't there", because p01 showed a cache write. That was wrong. The Portkey gateway config on this API key keeps its
   own **response cache**. When a request is byte-identical to an earlier one, Portkey returns the stored response
   (`x-portkey-cache-status: HIT`), including the old token counts, without calling the model. p01 and p08 were identical to the
   Problem 3 runs, so they came back with Problem 3's numbers, including that first call's cache write. The other 10 with_cache documents
   and all 24 cache-busted calls were real model calls. So the totals above count p01 and p08 at what they cost when first run, not
   at a fresh price (a fresh run would show cache reads). Since Problem 6, `agent.py` sends `x-portkey-cache-force-refresh` and
   logs `portkey_cache_status` on every call, so a replay can't hide in the numbers again.
3. **About 126 input tokens are never cached.** Every call shows exactly 126 uncached tokens. Caching works in fixed-size blocks of the
   prefix, so the tail end (the user message and leftover tokens that don't fill a block) is always billed at full price.
4. **The second call in a run writes a little more.** In with_cache, call 2 of each document writes about 160 tokens. That's the
   `read_document` result added onto the end of the prompt. Writes are incremental: only the new tail is written, and the shared prefix is read from cache.
5. **Once input is cached, output dominates.** Cached input costs $0.01/M while output costs $0.50/M (50×). So ~175 output tokens per
   document cost almost half as much as ~21,700 cached input tokens. To cut further, shorten the reason, or skip the extra round trip by
   pasting the document into the prompt instead of reading it with a tool.
6. **The cache-buster itself costs about 24 tokens per call.** without_cache shows ~570 more input tokens in total, which is the random ID line on each of the 24 calls.
