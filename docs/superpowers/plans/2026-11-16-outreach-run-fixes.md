# /outreach run fixes (#296)

From the Robinhood and Verkada runs on 2026-11-16.

1. **His link is his call.** The command never stops, skips or warns over the posting's terms, a full wave or the
   company's history; only a person's own stop drops them.
2. **Greenhouse hosts.** `posting_key` treats boards., job-boards. and their eu. forms as one host. Test:
   `test_mcp_draft_brief`.
3. **Pre-sales managers.** `role_of` drops solutions, sales, support, customer and field engineering titles before
   the manager check, by phrase, so "Software Engineer, Support Systems" stays. Test: `test_mcp_pick_people`.
4. **After the reveal.** The command and the skill drop anyone whose revealed employer or email domain is not the
   company's.

Deploy: no migration. Pull, `uv sync`, tests, kickstart the API; a new session loads the tools.
