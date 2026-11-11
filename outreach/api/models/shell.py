"""The shell summary: `ShellSummary` as in `web/src/api/types.ts`, with #33's `plan` and `winsUnseen`."""
from outreach.api.models.base import Wire
from outreach.api.models.health import JobRunSummary, SenderSummary
from outreach.api.models.pipeline import PipelineLine


class PlanProgress(Wire):
    done: int
    total: int


class ShellSummary(Wire):
    as_of: str
    picked_today: int  # picked rows of today's list (0 until the day's list is built)
    capacity_today: int
    unhandled_replies: int  # email threads whose reply he has not sorted
    drafts_to_review: int  # the Outbox's review rows
    linkedin_replied: int  # people the LinkedIn page lists: those who replied
    open_review_items: int
    no_go_count: int
    done_count: int
    applied_count: int  # postings he applied to (#218), the Applied list's rows
    sending: SenderSummary
    replies_this_month: int
    jobs: list[JobRunSummary]
    plan: PlanProgress  # today's plan: rows done of rows planned
    wins_unseen: int
    pipeline: PipelineLine | None  # the scorecard line; "Scorecard unavailable" (bad) when it could not be built
