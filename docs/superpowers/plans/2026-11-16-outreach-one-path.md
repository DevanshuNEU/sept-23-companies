# One outreach path, Knock half (#290)

**Goal:** `/outreach <job link>` goes from a pasted job to Outbox drafts with no further input from Devanshu. The
ruling is #9's "One outreach path ruling (2026-11-16)". The DevVault half (the `/outreach` and `/coldmail`
commands, retiring the company-file flow) is its own PR in the DevVault repo.

## Tasks

1. **Fresh before stale.** `outreach/drafts/fresh.py` holds one rule: a posting 30 days old or newer by its real
   date (posted date, else first seen), not senior, ranks before a stale one. The MCP's `open_postings` key and the
   API's `rank_postings` (company page, Outbox) both put it right after the posting's state. The LinkedIn list keeps
   the level order: its role feeds the request log's context, which this change leaves alone.
   Tests: `test_mcp_reads` (the Ataraxis case, the real date wins over first seen, a fresh senior role does not
   jump), `test_api_company` (the page names the fresh one).
2. **The pasted posting.** `draft_brief` takes `posting_url` or `posting_id`. A link matches loosely (no query,
   no trailing slash or `/application`). It must be an open posting of the person's company family, else
   `bad_input`. The brief's `posting` and `person.role_to_name` both name it; the `brief_id` does not change.
   Tests: `test_mcp_draft_brief`.
3. **Instructions that exist.** The MCP instructions name the skill by its full live checkout path and mention
   `posting_url` (under the 2,100 character cap). Test: `test_mcp_server`.
4. **The skill and CLAUDE.md.** The cold-email skill gets the job link path, the sure-address rule, the Apollo
   budget (5 credits per company, waterfall included, no asking), the research yes, the H-1B write-back and the
   report of who was dropped. CLAUDE.md gets the Apollo pre-approval and the skill's full path.

## Deploy

Python changed in the MCP and the API: pull, `uv sync`, tests, kickstart `com.devanshu.outreach-api`. The next
Claude Code session loads the new MCP tool input. No migration.

## Added the same day: pick_people (his ask: "fix the choosing in MCP")

The reach out ruling (#9, 2026-11-14) says who gets emailed, but only its design exists (docs on the `reach-out`
branch). Every session picked people by its own judgment. `pick_people` (`outreach/mcp/tools/picking.py`, reads
only) takes the people an Apollo search returned and the job link, and code picks in the ruling's order by size
band (Knock's size via `outreach/pipeline/sizes.py`, shared with the sender's pacing; else Apollo's count; unknown
uses the 50 to 200 order), the job's city, then state, then the US, and team words from the posting title. Roles are
taken in turn, so five picks are not five recruiters. Everyone left out gets a reason. The two-arm test, the reach
tables and the scheduled job stay in the reach-out design. Tests: `test_mcp_pick_people`.
