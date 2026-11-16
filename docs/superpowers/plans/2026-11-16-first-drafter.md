# Nightly first-email drafter (#298)

He loaded 3,251 Apollo people at 68 companies into Knock on 2026-11-16: records only, nothing drafted. The two
sending accounts start at 15 a day each, so drafting them all at once would leave thousands going stale. The drafter
copies the follow-up drafter (#142): code picks who, code builds each brief, the model writes only text, code saves.

1. **Package.** `outreach/first_drafter/`: `config.toml` and `config.py` (`[drafter] enabled` true, `model`
   claude-opus-5-5, `max_per_run` 30, `outbox_target` 60, `per_company` 2; `[run]` as the follow-up drafter's),
   `pick.py`, `run.py`, `prompt.md`, `__main__.py`. Test: `test_first_drafter`.
2. **Who, in code** (`pick.py`, read only, a guarded connection with no grants). The email channel, an address, no
   first email in any state and no first send on the touch. The company after merges is not closed, removed, held or
   no-go; the person is not no-go and never replied or said no on any channel. Held and replied are the sender's own
   stops, so a draft there could not go out and would only fill the Outbox.
3. **How many.** `max(0, outbox_target - waiting)`, waiting being first emails drafted or approved anywhere, capped by
   `max_per_run` and `--limit`. At most `per_company` at one company a run.
4. **Order.** Companies in turn, fewest first emails sent first, then fewest waiting (so the next night starts with
   the companies that got nobody), then name. Inside a company, `pick_people`'s #9 size order (`role_of`, `ORDER`,
   `band_of`, `company_sizes`). A title it would never pick, or an executive at a company over 200, goes last and is
   never dropped.
5. **Per person.** `draft_brief` parts 1 and 2 through `run_tool`; `voice_unreadable` stops the run, another refusal
   or `can_draft_email` false is counted and the run goes on. The model gets the brief and returns one JSON
   `{"subject", "body", "hook", "project"}`; `save_draft` saves it with the brief id. A `FIXABLE` problem (the text
   blocks and the first-email rules the save warns on) gets one retry with the problems shown; the better draft
   (fewer blocks, then fewer problems) is kept. A session limit, a login problem, a broken CLI or 3 failed calls in a
   row stop the run.
6. **Prompt.** The cold-email skill's "The email", "The subject" and step 8, condensed: one finished email, at most 120
   words, exactly 2 links, the signature exactly, the first line from their side, no visa line, the subject last.
7. **Job.** `launchd/com.devanshu.outreach-first-drafter.plist.template`, daily at 05:00, after the follow-up
   drafter's 04:30; added to the deploy tool's `NEW_JOBS`, optional. CLAUDE.md lists it.

Deploy: no migration. Pull, `uv sync`, tests, then `uv run python -m outreach.deploy plan`, read it, and
`uv run python -m outreach.deploy install`. Before the first night, in the live checkout:
`uv run python -m outreach.first_drafter run --dry-run` shows who would be drafted.
