# /outreach dry run fixes (#294)

A dry run on Canonical's Graduate Software Engineer job (2026-11-16, no credits, nothing written) found three gaps.

1. **Masked names.** `pick_people` matches a masked Apollo name ("Alec Co***y") to a person on record by first name
   plus the surname's visible start and tail. One hit is a match; two or more is never guessed, and the pick says
   "maybe ... in Knock". Tests: `test_mcp_pick_people`.
2. **Search by place.** Apollo's free search does not say where a person is, so `/outreach` searches the job's
   city, then its state, then the US, and sets each candidate's place from the search that found them.
3. **The command in this repo.** `.claude/commands/outreach.md` runs the path from a session in the live checkout.
   DevVault stays as he keeps it; its own `/outreach` is the old flow and is not used for email.

Also the free size probe: two `per_page` 1 people searches by employee range ("1,49", "50,200") tell the size band
when Knock has none (checked on Canonical, over 200, and Ataraxis AI, under 50).

Deploy: no migration. Pull, `uv sync`, tests, kickstart the API; start a new Claude Code session for the tools.
