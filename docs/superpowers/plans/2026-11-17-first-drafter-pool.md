# First drafter: only the loaded list, and non-engineering titles out (#300)

A read-only preview of the first drafter's queue on live data, after #299 merged and before its job was installed,
found two problems. Nothing was drafted.

1. **Only the loaded list.** `[drafter] loaded_since = "2026-11-16"`: only people created that day or later are in
   the pool, so older contacts (17 from the October import, 12 added by hand, a warm interview lead among them) are
   never drafted. Set it to the day of a new load; "" is everyone. Tests: `test_first_drafter`.
2. **Non-engineering titles out.** `role_of` drops program, project and product managers, scrum masters, HR and
   people partners, customer success, and recruiters for pharmacy, nursing, clinical, retail or store hiring. The
   drafter now skips any title `role_of` rejects (and executives at companies over 200) instead of drafting them
   last. Tests: `test_mcp_pick_people`, `test_first_drafter`.

On live data: 3,182 in the pool, 2,997 draftable at 67 companies.

Deploy: no migration. Pull, `uv sync`, tests, then `uv run python -m outreach.deploy install` for the job that #299
added (not installed yet), and `run --dry-run` once.
