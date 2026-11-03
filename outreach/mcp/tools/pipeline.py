"""The miss log (#153): `log_miss`, a job link Devanshu pasted because Knock did not show it.

The tool stores the link once with the first cause `outreach.pipeline.misses.diagnose` finds, and says the cause in
plain words. The diagnosis, its order and its causes are that module's; nothing here restates them. The only write is
one `miss` row: no event, no company, board or posting is touched. A second call with the same link returns the first
row. The note is Devanshu's own words and is stored as given.
"""
from __future__ import annotations

from pydantic import Field

from outreach.mcp.guard import Grant
from outreach.mcp.tools import Ctx, Input, ToolError, ToolSpec
from outreach.pipeline.misses import log_miss as store_miss
from outreach.store import transaction

__all__ = ["TOOLS"]


class LogMissInput(Input):
    url: str = Field(min_length=1, max_length=2000, description="The job link he pasted.")
    note: str = Field("", max_length=500, description="Where it came from or what he saw, in his words.")


def log_miss(ctx: Ctx, args: LogMissInput) -> dict:
    if not args.url.strip():
        raise ToolError("bad_input", "The link is empty.", "Send the job link he pasted. Nothing was written.")
    with transaction(ctx.conn):
        miss = store_miss(ctx.conn, args.url, args.note, ctx.now)
    return {"missId": miss.miss_id, "cause": miss.cause, "causeText": miss.cause_text, "postingId": miss.posting_id,
            "companyId": miss.company_id, "boardId": miss.board_id}


TOOLS = (
    ToolSpec("log_miss",
             "Record a job link Devanshu pasted because Knock did not show it, and say why in plain words: Knock "
             "does not know the company, has no board for it, has not read the job yet, read it and set it aside "
             "(level, not engineering, visa), already shows it, or the link is not a board Knock reads. One row per "
             "link: the same link again returns the first. Writes nothing else.",
             LogMissInput, log_miss, {"miss": Grant(insert=True)}),
)
