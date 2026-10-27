"""The people plan (#15): `GET /api/plan` and the writes on its rows and notes.

`POST /api/plan/{entryId}/request`, `/apply`, `/search` and `/skip` answer with the row as the plan shows it;
`POST /api/plan/{entryId}/pass` (Not now, top picks) answers with the company that came in from the bench;
`GET /api/plan/browse` (Browse companies, #120) is every company, paged; `GET /api/plan/bench` is the bench ("More options") and `POST /api/plan/bench/{companyId}/add` adds one of it;
`POST /api/plan/{entryId}/people` (company first: the people he asked, pasted) answers with the row and each person
logged; `PUT
/api/notes/{noteId}` and `POST /api/notes/{noteId}/confirm` answer with the note. Each is one `run_write`. See
`outreach.api.queries.plan` for how a row is derived and `outreach.api.services.plan` for the writes.
"""
from fastapi import APIRouter, Query, Request, Response

from outreach.api import views
from outreach.api.db import read_conn
from outreach.api.idempotency import run_write
from outreach.api.models.plan import BenchView, BrowseView, LogPeopleResult, NoteDraft, PassResult, PlanRow, PlanView
from outreach.api.models.plan_writes import (
    AddFromBenchBody, ConfirmReasonBody, LogAppliedBody, LogPeopleBody, LogRequestBody, PassPickBody, SaveNoteBody,
    SaveSearchBody, SkipRowBody,
)
from outreach.api.queries import bench as bench_queries
from outreach.api.queries import browse as browse_queries
from outreach.api.queries import plan as queries
from outreach.api.routers import MAX_ID, row_id
from outreach.api.services import picks
from outreach.api.services import plan as service
from outreach.drafts.checks import load_banned
from outreach.plan.browse import BrowseFilters
from outreach.plan.config import default_plan_config
from outreach.plan.drafts import me_unconfirmed

router = APIRouter(tags=["plan"])


@router.get("/plan", response_model=PlanView)
async def plan(request: Request) -> Response:
    def compute(now: str, _) -> PlanView:
        cfg = default_plan_config()
        with read_conn(request) as conn:
            return queries.plan_view(conn, now, cfg)

    return await views.serve(request, PlanView, compute)


@router.get("/plan/bench", response_model=BenchView)
async def bench(request: Request) -> Response:
    def compute(now: str, _) -> BenchView:
        cfg = default_plan_config()
        with read_conn(request) as conn:
            return bench_queries.bench_view(conn, now, cfg)

    return await views.serve(request, BenchView, compute)


@router.get("/plan/browse", response_model=BrowseView)
async def browse(request: Request, q: str = Query("", max_length=100), level_role: bool = Query(False, alias="levelRole"),
                 visa_ok: bool = Query(False, alias="visaOk"), unworked: bool = False,
                 after: int = Query(0, ge=0, le=MAX_ID), limit: int = Query(25, ge=1, le=50)) -> Response:
    """Browse companies (#120): every company the plan could pick, in stored rank order, one page at a time."""
    f = BrowseFilters(q=q, level_role=level_role, visa_ok=visa_ok, unworked=unworked)

    def compute(now: str, _) -> BrowseView:
        cfg = default_plan_config()
        with read_conn(request) as conn:
            return browse_queries.browse_view(conn, now, cfg, f, after, limit)

    return await views.serve(request, BrowseView, compute)


@router.post("/plan/{entry_id}/pass", response_model=PassResult)
def pass_pick(body: PassPickBody, request: Request, entry_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    return run_write(request, lambda ctx: picks.pass_pick(ctx, entry_id, body, cfg))


@router.post("/plan/bench/{company_id}/add", response_model=PlanRow)
def add_from_bench(body: AddFromBenchBody, request: Request, company_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    return run_write(request, lambda ctx: picks.add_from_bench(ctx, company_id, body, cfg))


def _banned(request: Request, cfg) -> list:
    """The note scopes of the banned list the API is configured with (read outside the write's transaction). An
    unusable list comes back empty, and #15's `check_note` blocks every note on that."""
    return load_banned(request.app.state.settings.banned_phrases, scopes=tuple(cfg.get("note", "banned_scopes")))[0]


@router.post("/plan/{entry_id}/request", response_model=PlanRow)
def log_request(body: LogRequestBody, request: Request, entry_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    return run_write(request, lambda ctx: service.log_request(ctx, entry_id, body, cfg))


@router.post("/plan/{entry_id}/apply", response_model=PlanRow)
def log_applied(body: LogAppliedBody, request: Request, entry_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    return run_write(request, lambda ctx: service.log_applied(ctx, entry_id, body, cfg))


@router.post("/plan/{entry_id}/people", response_model=LogPeopleResult)
def log_people(body: LogPeopleBody, request: Request, entry_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    banned, unconfirmed = _banned(request, cfg), me_unconfirmed()
    return run_write(request, lambda ctx: service.log_people(ctx, entry_id, body, cfg, banned, unconfirmed))


@router.post("/plan/{entry_id}/search", response_model=PlanRow)
def save_search(body: SaveSearchBody, request: Request, entry_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    return run_write(request, lambda ctx: service.save_search(ctx, entry_id, body.url, cfg))


@router.post("/plan/{entry_id}/skip", response_model=PlanRow)
def skip_row(body: SkipRowBody, request: Request, entry_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    return run_write(request, lambda ctx: service.skip_row(ctx, entry_id, body.revision, cfg))


@router.put("/notes/{note_id}", response_model=NoteDraft)
def save_note(body: SaveNoteBody, request: Request, note_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    banned = _banned(request, cfg)
    return run_write(request, lambda ctx: service.save_note(ctx, note_id, body.text, body.reason_line, body.revision,
                                                            cfg, banned))


@router.post("/notes/{note_id}/confirm", response_model=NoteDraft)
def confirm_reason(body: ConfirmReasonBody, request: Request, note_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    banned = _banned(request, cfg)
    return run_write(request, lambda ctx: service.confirm_note(ctx, note_id, body.revision, cfg, banned))
