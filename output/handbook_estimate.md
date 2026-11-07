# Handbook Size & Cost Estimate

The vendor desk agent sends `data/shop_handbook.md` to the model on every call, so its size sets the base cost of each call.

## Token Count & Methodology

- **Measured count: 10,058 tokens.** I counted `data/shop_handbook.md` with OpenAI's `tiktoken` library using the `o200k_base` encoding, which is the tokenizer used by current GPT models. (The older `cl100k_base` encoding gives 10,184.)
- **File size:** 28,903 characters (29,241 bytes), 338 lines, 6,127 words.
- **Rule-of-thumb check:** The common estimate of 1 token ≈ 4 characters gives 28,903 / 4 ≈ 7,226 tokens. That is **about 28% too low** for this file, because it is full of tables, codes, prices, and email addresses, which split into more tokens than plain prose. So I use the measured count below.
- **Caveat:** The billed prompt will be slightly larger than 10,058 tokens, because each call also sends the system instructions, the vendor document, and a few tokens of chat-message formatting. We will check the real number against the API's `usage` field in a later problem.

## Pricing Rates (`data/prices.json`, model `gpt-6-luna`)

| Rate | Per 1M tokens |
|---|---|
| Standard input | $0.10 |
| Cached input (cache hit) | $0.01 |
| Cache write | $0.125 |
| Output | $0.50 (not part of the handbook cost) |

## Cost Without Caching

- **1 call:** (10,058 / 1,000,000) × $0.10 = **$0.0010058**
- **1,000 calls:** 1,000 × $0.0010058 = **$1.0058**

## Cost With Caching (first call writes the cache)

- **1 call (cache write):** (10,058 / 1,000,000) × $0.125 = **$0.00125725**. This is 25% more than an uncached call, so caching does not pay off for a single call.
- **1,000 calls (1 write + 999 hits):** each hit costs (10,058 / 1,000,000) × $0.01 = $0.00010058.
  $0.00125725 + (999 × $0.00010058) = $0.00125725 + $0.10047942 = **$0.10173667**

## Cost With Caching (handbook already cached before the calls)

- **1 call (cache hit):** (10,058 / 1,000,000) × $0.01 = **$0.00010058**
- **1,000 calls (all hits):** 1,000 × $0.00010058 = **$0.10058**

## Summary

| Scenario | 1 call | 1,000 calls |
|---|---|---|
| No caching | $0.0010058 | $1.0058 |
| Caching, first call writes | $0.00125725 | $0.10173667 |
| Caching, already cached | $0.00010058 | $0.10058 |

Caching cuts the handbook's input cost over 1,000 calls by about **90%** ($1.0058 → $0.1017). It only works if the handbook sits at the very start of the prompt and stays exactly the same on every call, so the per-call vendor document must come after it.

### For comparison: the ÷4 estimate (7,226 tokens)

| Scenario | 1 call | 1,000 calls |
|---|---|---|
| No caching | $0.0007226 | $0.7226 |
| Caching, first call writes | $0.00090325 | $0.07309099 |
| Caching, already cached | $0.00007226 | $0.07226 |

These figures understate the real cost by about 28%.
