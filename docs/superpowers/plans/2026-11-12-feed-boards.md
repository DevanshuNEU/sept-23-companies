# Boards the Simplify feed names (#239)

**Goal:** Knock adds the company boards the SimplifyJobs new grad feed points at, on ATSs it already reads, so their jobs show up in Jobs.

**Why:** on 2026-11-12 Knock covered 219 of the feed's 531 listings of the last 30 days (41%). About 180 of the 312 misses were on a Greenhouse, Ashby, Lever, Workday, Workable, JazzHR or Rippling board Knock had no row for. A read-only preview found 214 such boards due (Workday 99, Ashby 29, Lever 24, Workable 18, JazzHR 18, Greenhouse 17, Rippling 9).

**Design:** reuse the ATS seed (#192). `outreach/sources/feed_seed.py` builds candidate rows from the active `feed_listing` rows (newest first, one per board, source `simplify`) and hands the due ones to `ats_seed._gate_rows`, which reads each board once, promotes one with an open US engineering role and records the verdict. The crawl tick calls it after the feed poll (`_feed_boards_step`), at most `feed_per_tick` (10, `ats_seed.toml`) a tick, within the tick's budget, through the tick's own client, and only on the ATSs it reads. A board known in any letter case or by alias sends nothing; a rejected or dead one waits `retry_days`.

**Tasks:**
1. Tests (`tests/test_sources_feed_seed.py`): promotion and rejection, known board in any case, off and unread ATSs, inactive and bad tokens, the limit and order, retry days and errors, a stapply verdict kept, name join, the stop check, the tick step and its failure path.
2. `ats_seed`: `_promote` and `_gate_rows` take the source, the event kind, a stop check and the tick's client as it is; `SeedConfig.feed_per_tick`.
3. `feed_seed.run_feed_seed`, the tick step, its summary fields and the tick's printed line.
4. Deploy, then measure Simplify coverage again after a day (`.context/sim/coverage.py`).
