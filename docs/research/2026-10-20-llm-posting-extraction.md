> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. Its recommendations feed `superpowers/specs/2026-10-20-opportunity-pipeline-design.md` and decide nothing on their own. Working files and raw data it mentions stay outside the repo in `~/Desktop/outreach-scratch/`.

# LLM extraction of job posting facts: API features, cost, reliability, design

Research note for the outreach job-search system. Read on 2026-10-20. Every web source below was read on that date unless a line says otherwise. Measurements come from read-only queries on `~/Desktop/DevVault/job-search/pipeline/radar.db` (opened with `file:...?mode=ro`); each one names the query.

## Summary

1. The cheap reader is now Claude Haiku 5.5 (released 2026-10-07), not Haiku 4.5. It costs $0.10 input and $0.50 output per million tokens, a tenth of Haiku 4.5 ($1 / $5).
2. A 2,000-token instruction prefix cannot be cached on Haiku 4.5: its minimum cacheable length is 4,096 tokens, and a shorter prefix is silently not cached. On Haiku 5.5 the minimum is 512, so it caches.
3. Measured volume: about 6,670 new postings a week (28,900 a month) after the July backfill. About 36% have engineering or intern titles (10,400 a month).
4. Cost per month to read every engineering or intern posting once: about $55 on Haiku 4.5 standard, $28 on Haiku 4.5 batch, $4.70 on Haiku 5.5 standard with caching, $2.40 on Haiku 5.5 batch with caching. Reading all postings costs about 2.8 times that.
5. Re-reading all 100,747 live postings once costs about $535 on Haiku 4.5 standard, $267 on Haiku 4.5 batch, and $23 to $35 on Haiku 5.5 batch.
6. The bigger gap is body text, not reading. Only 29,531 of 100,747 live postings (29%) have a stored body of 500 characters or more. Among engineering and intern postings first seen in the last 6 weeks, it is 34%. Most Workday and Oracle postings have no real body.
7. Structured outputs guarantee valid JSON but not true content. The Citations feature returns exact spans but cannot be combined with structured outputs (400 error). So the plan should be: JSON schema with a quote per field, and code checks each quote against the stored text.
8. Literature: no paper extracts sponsorship, citizenship, or student-only facts from postings. Papers on skill and requirement extraction show LLMs find the right place but miss exact spans, misattach years of experience, and flip negations. A labeled set of our own postings is the only evidence that will count.
9. To show one field's error rate is at most 2% (95% confidence) you need about 150 to 190 labeled postings of that field's class with zero errors. Rare classes such as "will sponsor" need stratified sampling to reach that count.
10. Recommended: Haiku 5.5 with thinking off, one call per new posting body, a pinned extractor version, phrase rules kept as a cross-check, and fail open (show the posting) whenever the reader is missing, unverified, or disagrees with the rules.

## 1. Anthropic API features that matter here

### 1.1 Models and prices

| Model | ID | Input $/MTok | Output $/MTok | 5m cache write | 1h cache write | Cache read | Batch input / output |
|---|---|---|---|---|---|---|---|
| Claude Haiku 5.5 (prompts up to 100k tokens) | `claude-haiku-5-5` | 0.10 | 0.50 | 0.125 | 0.20 | 0.01 | 0.05 / 0.25 |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 1.00 | 5.00 | 1.25 | 2.00 | 0.10 | 0.50 / 2.50 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2.00 | 10.00 | 2.50 | 4.00 | 0.10 | 1.00 / 5.00 |
| Claude Opus 5.5 | `claude-opus-5-5` | 4.00 | 20.00 | 5.00 | 8.00 | 0.20 | 2.00 / 10.00 |

Source: [Pricing](https://platform.claude.com/docs/en/about-claude/pricing), read 2026-10-20.

- Haiku 5.5 charges more for prompts over 100,000 tokens ($0.50 / $2.50). Our longest stored body is 235,859 characters, about 59,000 tokens, so no posting crosses that line. ([Haiku 5.5 overview](https://platform.claude.com/docs/en/models/haiku-5-5/overview); query in 2.1.)
- Haiku 5.5 was released 2026-10-07. It is described as "for high-volume, latency-sensitive tasks such as classification, extraction, and routing". Retirement is not sooner than 2027-10-07. ([Haiku 5.5 overview](https://platform.claude.com/docs/en/models/haiku-5-5/overview))
- Haiku 4.5 is now listed under "Legacy models (still available)". ([Models overview](https://platform.claude.com/docs/en/about-claude/models/overview))
- Haiku 5.5 uses the newer tokenizer: "the same input text produces approximately 30% more tokens on Claude Haiku 5.5 than on Claude Haiku 4.5." ([Haiku 5.5 migration guide](https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide))
- Every Claude model ID is a pinned snapshot, including the dateless IDs. So `claude-haiku-5-5` will not change under us. ([Models overview](https://platform.claude.com/docs/en/about-claude/models/overview))
- US-only inference (`inference_geo: "us"`) costs 1.1x on Claude 4.6 and later models. The default global routing costs the standard price. ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing))

Haiku 5.5 behaviors that matter for extraction ([migration guide](https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide), [Prompting Claude Haiku 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5), [Errors](https://platform.claude.com/docs/en/api/errors)):

- Thinking is on by default and billed as output. It can be turned off with `thinking: {"type": "disabled"}` at `low`, `medium`, or `high` effort. Telling the model to answer directly in the prompt did not stop it from thinking in Anthropic's tests.
- `temperature`, `top_p` and `top_k` must be omitted. There is no temperature 0, so two reads of the same posting can differ. Plan for that.
- Assistant prefill returns a 400. Use structured outputs for the JSON shape.
- Forced `tool_choice` is accepted, but then the response has no thinking block.
- It runs safety classifiers and can return `stop_reason: "refusal"`, with no server-side fallback. Job postings should rarely trigger this, but the code must treat a refusal as "not read".

### 1.2 Message Batches API

From [Batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing) and [Rate limits](https://platform.claude.com/docs/en/api/rate-limits):

- 50% off input and output tokens.
- Up to 100,000 requests or 256 MB per batch. Most batches finish within 1 hour. A batch expires if not done in 24 hours.
- Results stay downloadable for 29 days. Results come back in any order, so key them by `custom_id`.
- `custom_id` must match `^[a-zA-Z0-9_-]{1,64}$`.
- `errored`, `canceled` and `expired` requests are not billed.
- Structured outputs and tool use work inside batches. `stream` and `max_tokens: 0` do not.
- Prompt caching works in batches and the discounts stack, but hits are "best-effort": "Users typically experience cache hit rates ranging from 30% to 98%". The docs suggest the 1-hour cache for batches.
- Batch queue limits by tier: Start 200,000 requests in the queue, Build 300,000, Scale 500,000; 100,000 per batch on every tier.
- Batch results are stored for 29 days, so the Batch API is not eligible for zero data retention. ([API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention))

### 1.3 Prompt caching

From [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) and [Pricing](https://platform.claude.com/docs/en/about-claude/pricing):

| Model | Minimum cacheable prompt |
|---|---|
| Claude Haiku 5.5 | 512 tokens |
| Claude Haiku 4.5 | 4,096 tokens |
| Claude Sonnet 5.5 | 512 tokens |
| Claude Opus 5.5 | 512 tokens |

- Below the minimum, "no error is returned". Check `cache_creation_input_tokens` and `cache_read_input_tokens` in `usage`; both 0 means no cache.
- TTL is 5 minutes (write costs 1.25x input) or 1 hour (write costs 2x). A cache read costs 0.1x input on Haiku models. Each hit refreshes the TTL at no cost.
- Up to 4 cache breakpoints per request. Caches are isolated per workspace on the Claude API.
- On most models, cache reads do not count toward the input-tokens-per-minute limit. ([Rate limits](https://platform.claude.com/docs/en/api/rate-limits))

What this means: on Haiku 4.5 the 2,000-token prefix is never cached. Growing it to 4,096 tokens with worked examples makes it cacheable and cheaper than the uncached 2,000-token prefix (see the cost table). On Haiku 5.5 the 2,000-token prefix (about 2,600 tokens on its tokenizer) caches as is.

### 1.4 Structured outputs and tool use

From [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs):

- `output_config.format` with a JSON schema, and `strict: true` on tools. Both are supported on `claude-haiku-5-5` and `claude-haiku-4-5-20251001`.
- Constrained decoding: always valid JSON, all required fields present, correct types.
- Supported: `enum`, `const`, `anyOf`, `required`, `additionalProperties: false` (required on objects), simple `pattern`. Not supported: `minimum`/`maximum`, `minLength`/`maxLength`, recursive schemas. Limits: 24 optional parameters in total, 16 union-type parameters.
- Enum values may come back differing only in capitalization. Compare case-insensitively.
- The output may not match the schema when `stop_reason` is `refusal` or `max_tokens`.
- Compiled grammars are cached for 24 hours. The first request after a schema change is slower.
- The docs state plainly that schema-valid output can still be "factually incorrect, hallucinated, or nonsensical". This is why quotes must be checked by code.
- Tool use adds a hidden system prompt: 496 tokens on Haiku 4.5 with `tool_choice` auto, 588 with a forced tool. ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing))

### 1.5 Citations

From [Citations](https://platform.claude.com/docs/en/build-with-claude/citations):

- "All active models support citations."
- Plain text documents are split into sentences. A citation carries `cited_text` plus `char_location` (`start_char_index`, `end_char_index`, 0-indexed) into the document you sent.
- "Citations are guaranteed to contain valid pointers to the provided documents."
- `cited_text` "does not count toward output tokens". Input tokens rise slightly for the added system prompt and chunking.
- Works with prompt caching, token counting and batches.
- Incompatible with structured outputs: enabling citations together with `output_config.format` returns a 400.

What this means: Citations gives exact spans for free, but the answer is prose with citation blocks, not a typed JSON object. For this system the better primary path is a JSON schema with a quote field, checked by our own code. Citations is a good second reader for hard cases, where the guaranteed span is worth parsing prose.

### 1.6 Rate limits

From [Rate limits](https://platform.claude.com/docs/en/api/rate-limits). Tiers are now named Start, Build, Scale and Custom, with monthly spend caps of $500, $1,000 and $200,000.

| Tier | Haiku 5.5 and Haiku 4.5 RPM | Input tokens per minute | Output tokens per minute |
|---|---|---|---|
| Start | 1,000 | 2,000,000 | 400,000 |
| Build | 5,000 | 5,000,000 | 1,000,000 |
| Scale | 10,000 | 10,000,000 | 2,000,000 |

- New organizations may start in an Evaluation tier below these numbers.
- A 429 carries `retry-after` when it is a rate limit. Reaching the tier's monthly spend cap also returns 429, with no `retry-after`, and `error.details.error_code` is `enforced_spend_limit_reached`. A spend limit you set yourself returns a 400.
- Our peak is about 4,600 new postings in a day (2026-08-19). Even at Start tier that is under 5 minutes of request budget. Rate limits are not a constraint here.

### 1.7 Data retention and training

- "Retained data is never used for model training without your express permission." ([API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention))
- Commercial default: "we automatically delete inputs and outputs on our backend within 30 days of receipt or generation". If flagged for a usage policy issue, inputs and outputs can be kept up to 2 years. Page last updated 2026-07-01. ([Commercial data retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data))
- Batch results: 29 days. Zero data retention needs a sales agreement and does not cover batches.
- Job postings are public text, so retention risk is low. Do not send anything from Devanshu's contacts or Gmail in these requests.

### 1.8 Token counting and errors

- `POST /v1/messages/count_tokens` is free, has its own rate limit (Start 5,000 RPM), and returns an estimate. It counts with the tokenizer of the `model` you pass. ([Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting))
- Errors: 429 rate limit, 500 `api_error` (retry with backoff), 529 `overloaded_error`, 413 too large (Messages 32 MB, Batch 256 MB). The SDKs retry connection errors, 429 and 5xx twice by default, honoring `retry-after`. Every response has a `request-id` header. ([Errors](https://platform.claude.com/docs/en/api/errors))
- The docs list no idempotency key for Messages or Batches. Idempotency must come from our own keys (section 5.5).

## 2. Cost model from radar.db

### 2.1 Measurements

All queries ran read-only on 2026-10-20 against `radar.db` (694 MB, last written 2026-10-20 20:00).

| What | Value | Query |
|---|---|---|
| Postings | 104,689 total, 100,747 not merged | `select count(*), sum(merged_into is null) from posting` |
| First seen range | 2026-07-28 to 2026-10-21 | `select min(first_seen_at), max(first_seen_at) from posting` |
| Postings with any stored body | 46,090 | longest `body_text` per posting, `posting_version join posting where merged_into is null and body_text is not null group by posting_id` |
| Body length, all stored bodies (chars) | p25 133, median 2,082, mean 3,256, p90 7,591, p99 11,896, max 235,859 | same query, sorted |
| Bodies under 200 chars | 13,665 (mostly Oracle ORC stubs, mean 210 chars) | same query, grouped by `board.ats` |
| Postings with a real body (500+ chars) | 29,531 | same query, `length >= 500` |
| Real body length (chars) | median 5,091, mean 4,996, p90 8,326, p99 12,817 | same |
| Live postings with no stored body | Workday 28,233, Greenhouse 13,342, Oracle 9,816, Eightfold 2,106, SmartRecruiters 1,140 | `posting join board where posting_id not in (select posting_id from posting_version where body_text is not null) group by ats` |
| New postings per week, 2026-09-07 to 2026-10-18 | 6,153 to 7,101, total 39,994 over 6 weeks (6,666 a week) | `select strftime('%Y-%W', first_seen_at), count(*) from posting where merged_into is null group by 1` |
| New postings per day | about 1,000 to 1,600 on weekdays, 60 to 220 on weekends; spikes of 3,400 to 4,600 when boards are added (2026-08-19, 2026-09-01) | same, by `substr(first_seen_at,1,10)` |
| Initial backfill | 14,622 on 2026-07-28 | same |
| Title lane, all live postings | engineering 32,724, intern 1,999, off lane 7,812, not engineering 58,167, general application 45 | `outreach.rules.title.classify_title` from this repo, run over `posting.title` |
| Title lane, 6 weeks from 2026-09-07 | engineering 13,299, intern 1,091 (36% together), of 39,994 | same, filtered on `first_seen_at` |
| Real body share, engineering and intern, 6 weeks | 4,831 of 14,390 (34%) | same, joined to longest body per posting |
| `label` table rows | 0 | `select count(*) from label` |

Volumes used below, at 52/12 weeks a month: 10,393 engineering and intern postings a month, 28,885 postings a month in total, and 100,747 live postings for a one-time re-read.

### 2.2 Token method

- Anthropic's own count comes from `POST /v1/messages/count_tokens` with the target `model` (free, estimate). That call was not made, because the brief forbids calling the API. ([Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting))
- Instead: 4 characters per token for Haiku 4.5, as the brief allowed, and 1.3 times that token count for Haiku 5.5, from the migration guide.
- Check on that assumption: the models overview says 1M tokens on the new tokenizer is about 2.5M characters (2.5 characters per token) for general text. At 2.5 characters per token, the Haiku 5.5 batch cached cost per 1,000 postings rises from $0.23 to $0.25. The conclusion does not change.
- Per posting, Haiku 4.5 tokens: prefix 2,000; body 4,996 / 4 + 60 for title, company, and location = 1,309; output 400 (about 10 fields, each with a value and a short quote). Haiku 5.5: the same times 1.3, with thinking turned off.
- Cache writes are left out. With a steady stream there is about one write per run, a few tenths of a cent.

### 2.3 Cost table

US dollars. "Eng month" is 10,393 engineering and intern postings. "All month" is 28,885 postings. "Once" is all 100,747 live postings, assuming each gets a real body.

| Setup | Per 1,000 postings | Eng month | All month | Once (100,747) |
|---|---|---|---|---|
| Haiku 4.5 standard, 2,000-token prefix (cannot cache: under the 4,096 minimum) | 5.31 | 55.18 | 153.35 | 534.87 |
| Haiku 4.5 batch, 2,000-token prefix, no cache | 2.65 | 27.59 | 76.67 | 267.43 |
| Haiku 4.5 standard, prefix grown to 4,096 and cached | 3.72 | 38.65 | 107.41 | 374.64 |
| Haiku 4.5 batch, 4,096 cached, 30% hit | 3.15 | 32.73 | 90.97 | 317.31 |
| Haiku 4.5 batch, 4,096 cached, 98% hit | 1.90 | 19.71 | 54.77 | 191.03 |
| Haiku 5.5 standard, no cache | 0.69 | 7.17 | 19.94 | 69.53 |
| Haiku 5.5 standard, 2,000-token prefix cached | 0.46 | 4.74 | 13.18 | 45.96 |
| Haiku 5.5 batch, no cache | 0.35 | 3.59 | 9.97 | 34.77 |
| Haiku 5.5 batch, cached, 98% hit | 0.23 | 2.39 | 6.66 | 23.21 |
| Sonnet 5.5 standard, cached (second reader only) | 8.86 | 92.12 | 256.02 | 892.96 |
| Reference: OpenAI gpt-5-nano standard, cached, no reasoning tokens counted | 0.24 | 2.45 | 6.80 | 23.72 |
| Reference: Gemini 2.5 Flash-Lite standard, no cache | 0.49 | 5.10 | 14.18 | 49.46 |

Sources for the reference rows: OpenAI gpt-5-nano is $0.05 input, $0.005 cached input, $0.40 output, with batch at half ([OpenAI pricing](https://developers.openai.com/api/docs/pricing), page shows no date). Gemini 2.5 Flash-Lite is $0.10 input, $0.40 output, batch $0.05 / $0.20, and paid-tier data is not used to improve Google products ([Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing), last updated 2026-10-19). Both use their own tokenizers, and gpt-5-nano is a reasoning model whose reasoning tokens are billed as output. They are references, not a recommendation.

Notes:
- If Haiku 5.5 thinking stays on at low effort, add roughly 300 output tokens. That puts standard, no-cache, all postings at about $24 a month instead of $20. This is my estimate, not a measured number.
- A second reader for hide candidates only (for example 5% of postings on Sonnet 5.5) adds about $13 a month at all-postings volume.
- The one-time re-read can only cover postings with a stored body. Today that is 29,531 real bodies, so a re-read of what exists costs about 29% of the "Once" column.

## 3. Reliability: what is known

### 3.1 Sources

1. Nguyen, Zhang, Montariol, Bosselut (2024), "Rethinking Skill Extraction in the Job Market Domain using Large Language Models", NLP4HR at EACL 2024, [arXiv 2402.03832](https://arxiv.org/abs/2402.03832). They ran few-shot GPT-3.5 and GPT-4 against supervised models on 6 skill datasets. GPT-4 trailed supervised models on most of them (SkillSpan strict F1 27.8 against 62.6). Under relaxed span matching GPT-3.5 averaged 58.0 F1, so the model found the right place but got the exact span wrong. Lesson: normalize text before checking quotes, and judge span boundaries loosely.
2. Clavié and Soulié (2023), "Large Language Models as Batteries-Included Zero-Shot ESCO Skills Matchers", [arXiv 2307.03539](https://arxiv.org/abs/2307.03539). They matched job ads to the fixed ESCO skill list. GPT-4 re-ranking gave more than +22 RP@10. Lesson: a closed list of values per field works better than free text.
3. Thakrar and Young (2025), "Enhancing Talent Employment Insights Through Feature Extraction with LLM Finetuning", [arXiv 2501.07663](https://arxiv.org/abs/2501.07663). They extracted remote type, pay, experience and education from 1.2M postings. F1 was 0.78 for remote and 0.59 for experience. Caveat: the labels were made by Gemini 1.5 Flash, not people. The failure modes they report match ours: years attached to the wrong clause, and flipped negation ("you do not need a Bachelor's degree" read as a requirement).
4. Anthropic, "Reduce hallucinations", [docs](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations). It gives guidance, not numbers: allow "I don't know", extract word-for-word quotes first, make each claim cite a quote, and "If it can't find a quote, it must retract the claim". It also suggests running the same prompt several times and comparing the answers.
5. Liu, Zhang, Liang (2023), "Evaluating Verifiability in Generative Search Engines", [arXiv 2304.09848](https://arxiv.org/abs/2304.09848). Only 51.5% of generated sentences were fully supported by their citations, and 74.5% of citations supported their sentence. Lesson: never trust a model's quote without a code check. A quote that passes the check can still fail to support the value.
6. Wang et al. (2022), "Self-Consistency Improves Chain of Thought Reasoning in Language Models", [arXiv 2203.11171](https://arxiv.org/abs/2203.11171). Majority vote over several samples gained 3.9 to 17.9 points on reasoning benchmarks. These benchmarks are not extraction tasks. It fits here because Haiku 5.5 has no temperature control: a second read of a hide candidate is a cheap agreement check.
7. Tam et al. (2024), "Let Me Speak Freely? A Study on the Impact of Format Restrictions on Performance of Large Language Models", [arXiv 2408.02442](https://arxiv.org/abs/2408.02442). JSON mode hurt reasoning tasks (GSM8K 49.3 against about 76 for GPT-3.5-Turbo) but helped classification (DDXPlus 60.3 against 41.6). Most of our fields are classification. Years and level involve a little reasoning, so give those fields a short `reason` string before the value, or keep adaptive thinking on for them. The models tested are from 2024.
8. Kadavath et al. (2022), "Language Models (Mostly) Know What They Know", [arXiv 2207.05221](https://arxiv.org/abs/2207.05221). Large models were fairly well calibrated about their own correctness. This supports an explicit "not stated" value. It does not support replacing the quote check with self-reported confidence.

Gap: no paper or vendor cookbook found extracts visa sponsorship, citizenship, clearance or student-only facts from postings. (Literature search done by a sub-agent on 2026-10-20, with each source page fetched.)

### 3.2 Where the current rules stand

The brief's own review found 12 of 179 "yes sponsor" wrong (6.7%, 95% Wilson interval 3.9% to 11.4%) and 6 of 31 student exclusions wrong (19.4%, interval 9.2% to 36.3%). The intervals are my arithmetic. With only 31 student cases, the true rate could be anywhere from 1 in 11 to 1 in 3.

### 3.3 How to evaluate

Sample size, per field and per value, using standard binomial bounds ([NIST e-Handbook 7.2.4.1](https://itl.nist.gov/div898/handbook/prc/section2/prc241.htm) for the Wilson interval; the rule of three, 95% upper bound about 3/N with 0 errors, as restated in [Newman, JAMA 1995](https://jamanetwork.com/journals/jama/article-abstract/389714)):

| Goal at 95% confidence | Errors seen | Labeled items needed |
|---|---|---|
| Error rate at most 2%, rule of three | 0 | 150 |
| Error rate at most 2%, two-sided Wilson | 0 | 189 |
| Error rate at most 2%, exact one-sided | 1 | about 237 |
| Estimate a rate near 2% to plus or minus 1 point | any | about 753 |

Arithmetic mine: Wilson with 0 errors gives an upper bound of z²/(n+z²), and 3.84/(189+3.84) = 1.99%.

How to apply it:
- Count per value, not per posting. "Will sponsor" shows up in about 2% of postings (179 in 8,000), so a random sample of 190 would hold about 4 of them. Sample by stratum. `radar.db` already has a `label` table with `stratum` and `sample_weight` for Horvitz-Thompson weighting. It is empty today.
- The costly mistake is a false hide. For every value that can hide a posting (`will_not_sponsor`, `citizen_or_clearance_required`, `current_student_only`, `years_min > 3`, `not_engineering`), measure precision first: of the postings the reader says to hide, how many should be hidden. Recall on those values matters less, because a miss only shows an extra posting.
- For the values that can show a posting (`will_sponsor`, `new_grad`), measure both precision and recall.
- First gold set, about 400 postings: 100 random; 100 where the reader and the current rules disagree; 200 spread over the rare hide and show values (about 30 or more each). Devanshu labels them. Store each with its body text, `content_hash`, the expected facts, and the quote spans.
- Regression set: freeze the gold set as a fixture in the repo (text plus expected facts). Unit tests run the deterministic rules over stored reader outputs. They never call the API, matching the repo rule that tests do not crawl or send. A separate eval script, run by hand, calls the API on the gold set when the prompt or model changes, and reports per-field precision and recall plus the quote-check pass rate. A change ships only if no hide-value precision drops.
- Add every misread found in use to the gold set with its correct label, the same way every bug fix starts with a test.

## 4. What existing job-search tools do

PLACEHOLDER

## 5. Design guidance

### 5.1 Extraction schema

One JSON object per posting body. Every fact field is an object `{value, quote}`. `quote` is copied word for word from the posting, at most about 300 characters, and is the empty string when `value` is `not_stated`. Closed lists only, each with an explicit "not stated".

| Field | Allowed values | Notes |
|---|---|---|
| `role_kind` | `software_engineering`, `other_engineering`, `not_engineering`, `not_stated` | SWE, ML, data and infra count as software. Hardware, mechanical and sales engineering count as other. |
| `employment_type` | `full_time`, `internship`, `co_op`, `contract`, `part_time`, `not_stated` | |
| `level_stated` | `intern`, `new_grad`, `early_career`, `mid`, `senior_or_above`, `not_stated` | From the title or body wording, not inferred from years. |
| `years_min` | integer or null, plus `years_kind`: `required`, `preferred`, `in_lieu_of_degree`, `not_stated` | The smallest required number in the main requirement. Quote the sentence. |
| `grad_window` | free text such as `"Dec 2025 - Aug 2026"` or `not_stated` | For new grad and intern postings that name graduation dates. |
| `sponsorship` | `will_sponsor`, `will_not_sponsor`, `not_stated`, `unclear` | `will_not_sponsor` only when the text says so, for example "unable to sponsor" or "must not require sponsorship now or in the future". "Authorized to work in the US" alone is `not_stated`. |
| `work_authorization` | `us_citizen_required`, `clearance_required`, `citizen_or_green_card_required`, `any_us_work_authorization`, `not_stated` | Keep this apart from sponsorship. ITAR or export-control wording without "citizen" is `not_stated` plus a flag. |
| `student_status` | `current_student_required`, `recent_grads_allowed`, `not_stated` | `current_student_required` needs wording like "must be enrolled" or "returning to school after the internship". |
| `locations` | list of `{city, region, country, quote}` | |
| `remote` | `remote_us`, `remote_global`, `hybrid`, `onsite`, `not_stated` | |
| `notes_for_human` | short string or empty | Anything odd, such as conflicting statements. |

Schema rules: `additionalProperties: false`, every field required, and `null` allowed through `anyOf` only where needed. That stays under the 24-optional and 16-union limits in 1.4. Compare enum values case-insensitively.

### 5.2 Prompt pattern

- System prompt (the cached prefix): the field definitions above, the rule "copy the quote exactly from the posting text; if you cannot find a sentence that states it, use `not_stated`", the sponsorship and authorization edge cases written out, and 4 to 6 short worked examples taken from the misreads in the whole-branch review. This follows the Anthropic hallucination guidance in 3.1 (quotes first, allow unknown, withdraw a claim with no quote).
- User message: a header with title, company and location, then the body inside `<posting>` tags. Nothing volatile goes in the system prompt, such as dates or IDs, because that would break the cache ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
- Request: `claude-haiku-5-5`, `output_config.format` with the schema, `thinking: {"type": "disabled"}`, `effort: "low"`, and `max_tokens` around 1,500. If the eval shows years or level misreads, try adaptive thinking at `low` effort on those fields first, and measure the cost.
- Batch API for backfills and re-reads. For the daily flow either works, since nothing here needs answers in seconds. A batch sent after each radar run usually returns within the hour at half price.

### 5.3 Quote check in code

1. Normalize both posting text and quote the same way: Unicode NFKC, curly quotes and all dash forms to ASCII, HTML entities decoded, whitespace collapsed, case folded.
2. The quote must be a substring of the normalized body (or of the title or location header). Store the matched character offsets.
3. If there is no exact match, allow one fallback: the quote's tokens appear in order within a short window. Mark these `quote_fuzzy` and never let a fuzzy quote hide a posting.
4. Field result: `verified`, `fuzzy`, or `unverified`. An unverified quote turns the value into `not_stated` for decisions and keeps the raw output for review.
5. Negation guard: for a hide value, the quote itself must contain the deciding words ("not", "unable", "citizen", "enrolled", the number of years). If the model's quote lacks them, the fact is unverified.

### 5.4 Combining with the phrase rules

- The reader is primary for facts. `decide.py` stays the one rules function, and it decides from facts: reader facts where verified, rule facts otherwise.
- The existing phrase rules (`sponsorship.py`, `eligibility.py`, `years.py`, `title.py`) still run on every posting and become a cross-check. For each field, store both answers.
- Agreement table per field. Both say hide: hide, with the reader quote shown in the UI. Reader says hide and rules say nothing: hide only if the quote is verified and passes the negation guard; otherwise show and queue it. Rules say hide and reader says `not_stated`: show and queue it. This is the case where the rules misread today. They disagree on value (for example sponsor against no-sponsor): show and queue it.
- Fail open everywhere: no reader output, refusal, `max_tokens` stop, unverified quote, or disagreement all mean the posting stays visible. This follows the opportunity-first rule: never hide a reachable company.
- The human queue shows the posting text with both quotes highlighted and three buttons. Each answer becomes a gold-set label (section 3.3).
- The title lane can still skip clearly non-engineering titles to save money, but at Haiku 5.5 prices reading everything costs about $7 to $20 a month. Reading all postings also catches title-rule misses.

### 5.5 Versioning, idempotency, re-runs

- `extractor_version` = hash of (model ID, system prompt text, JSON schema, request settings). Model IDs are pinned snapshots (1.1), so the version hash fully defines the reader.
- Store each result keyed by (`posting_version.content_hash`, `extractor_version`). Keep the raw response, `usage`, `stop_reason`, `request-id`, and the quote check results. The same body under the same version is never read twice. That key is the idempotency key, because the API offers none (1.8).
- Batch `custom_id` = first 64 characters of a hex hash of that key (it must match `^[a-zA-Z0-9_-]{1,64}$`). Results come back in any order, so match on `custom_id` only.
- Two kinds of change. A change to the rules (`decide.py`, `config.toml`) re-decides from stored facts with no API call, the same as `ruleset_version` today. A change to the prompt, schema or model creates a new `extractor_version`. Run it over stored `body_text` as a batch, compare against the old version on the gold set, then switch. Keep old rows for diffing.
- A changed posting body gets a new `content_hash`, and only that version is re-read.

### 5.6 Cost and failure handling

- Set a spend limit in the Console below the tier cap, for example $30 a month. Reaching it returns a 400 with "You have reached your specified API usage limits". Treat that as "reader paused", not as an error to retry. ([Rate limits](https://platform.claude.com/docs/en/api/rate-limits))
- Retries: keep the SDK default (2 retries on connection errors, 429 and 5xx, honoring `retry-after`). Anything still failing goes to an outbox row with exponential backoff, as the existing `outbox` and `body_attempt` tables already do. Never block a radar run on the reader.
- 429 with `enforced_spend_limit_reached`: stop until the month resets. 529 overloaded: back off and send the rest as a batch.
- Batch results: `errored` and `expired` are not billed, so resubmit them in the next batch. A batch past 24 hours expires, so check for results on every run.
- Every refusal, `max_tokens` stop or schema mismatch: no facts stored, the posting stays visible, and it is counted in a daily health line. If the share of unread postings passes a few percent, that is a bug to look at.
- Track cost per day from `usage` in the stored rows. Check `cache_read_input_tokens` is non-zero, which proves the prefix is caching.

### 5.7 Order of work

1. Fix body coverage first: 66% of recent engineering postings have no real body for any reader to read (2.1).
2. Label the first gold set (3.3) and run the current rules on it, to get a baseline per field.
3. Build the reader on Haiku 5.5 behind `extractor_version`, run it on the gold set with the eval script, and compare against the baseline.
4. Back-fill stored bodies with one batch (about $10 to $25), then turn on the daily reader in shadow mode (store and compare, decide nothing) for two weeks.
5. Switch decisions to reader-first with fail-open rules once hide-value precision on the gold set has an upper bound under 2%.

## Not verified

- Actual token counts for our prompt and postings on either Haiku tokenizer. The count_tokens endpoint is free, but the brief forbids API calls. The table uses 4 characters per token and the documented 1.3x factor.
- Output length per posting (400 tokens assumed) and how much thinking Haiku 5.5 does at `low` effort with thinking on.
- Haiku 5.5 accuracy on this task. It is 13 days old. No public extraction benchmark for it was found, and the system card was not read.
- How often Haiku 5.5 safety classifiers refuse ordinary job postings. Only the categories are documented.
- Batch cache hit rate for our traffic. The docs give 30% to 98%.
- Whether the Evaluation tier applies to Devanshu's API organization, and its limits.
- OpenAI and Gemini prices were read through a fetch summary of their pricing pages. The OpenAI page showed no date. Neither was checked against a second source.
- The rule-of-three original (Hanley and Lippman-Hand, JAMA 1983) was not opened. Newman 1995 was used instead.
- Thakrar and Young's numbers are measured against model-made labels, not human labels.
- Claims in section 4 that come only from vendor marketing pages are marked there.
- The 12 of 179 and 6 of 31 error counts come from the brief and were not re-measured here.
