"""`h1b_history` (read): what the H-1B list says about one company (#252).

The list is `<store folder>/h1b/companies.md`, built by `python -m outreach.h1b build` from USCIS's H-1B Employer
Data Hub. This tool finds the company as `contact_check` does (by name or id, merges followed, a near name or a tie is
a question and never a guess) and reads its line: approvals per fiscal year, the trend and the USCIS employer names
the build matched. A company the list does not hold has no H-1B on record. With no list built it is a plain error,
never an empty answer. It writes nothing.
"""
from __future__ import annotations

from pydantic import Field, model_validator

from outreach.h1b import lookup
from outreach.mcp.tools import Ctx, Input, ToolError, ToolSpec
from outreach.mcp.tools.bridge import _name_index, find_company
from outreach.mcp.tools.reads import MAX_ID, Family, company_row, marks
from outreach.store.writes import NAME_ALIAS_SOURCES

__all__ = ["TOOLS"]

SOURCE = "USCIS H-1B Employer Data Hub (initial plus continuing approvals, per fiscal year)"


class H1bHistoryInput(Input):
    company: str | None = Field(None, min_length=1, max_length=200, description="The company's name as he has it.")
    company_id: int | None = Field(None, ge=1, le=MAX_ID, description="A company id (wins over the name).")

    @model_validator(mode="after")
    def one_company(self):
        if self.company is None and self.company_id is None:
            raise ValueError("give company or company_id")
        return self


def _names(ctx: Ctx, family: Family, survivor: int) -> list[str]:
    """The company's own name, then its name aliases and its merged companies' names: what the list may be under."""
    ids = family.members(survivor)
    names = [company_row(ctx.conn, survivor)["name"]]
    names += [r["name"] for r in ctx.conn.execute(
        f"SELECT name FROM company WHERE company_id IN ({marks(ids)}) ORDER BY company_id", ids)]
    src = ", ".join("?" for _ in NAME_ALIAS_SOURCES)
    names += [r["alias"] for r in ctx.conn.execute(
        f"SELECT alias FROM company_alias WHERE company_id IN ({marks(ids)}) AND source IN ({src}) "
        f"ORDER BY alias_key", [*ids, *NAME_ALIAS_SOURCES])]
    return list(dict.fromkeys(names))


def h1b_history(ctx: Ctx, args: H1bHistoryInput) -> dict:
    if not lookup.has_list():
        raise ToolError("no_list", "No H-1B list is built, so there is nothing to say about any company.",
                        "In the live checkout run `uv run python -m outreach.h1b build`, then ask again.")
    built = lookup.built_on()
    family = Family(ctx.conn)
    found = find_company(ctx.conn, family, _name_index(ctx.conn, family), name=args.company,
                         company_id=args.company_id)
    company = {"resolution": found.resolution, "id": found.survivor, "name": None, "message": found.message,
               "candidates": found.candidates, "merged_note": found.merged_note}
    list_info = {"built_on": built, "source": SOURCE}
    if found.survivor is None:
        return {"company": company, "list": list_info, "h1b": None, "message": found.message}
    company["name"] = company_row(ctx.conn, found.survivor)["name"]
    tag = next((t for t in (lookup.tag_for(n) for n in _names(ctx, family, found.survivor)) if t and t.total), None)
    if tag is None:
        return {"company": company, "list": list_info,
                "h1b": {"on_record": False, "label": lookup.NO_RECORD, "total": 0, "trend": "none", "by_year": [],
                        "uscis_names": []},
                "message": f"The H-1B list built on {built} has no line for {company['name']}: no H-1B on record."}
    return {"company": company, "list": list_info,
            "h1b": {"on_record": True, "label": tag.label, "total": tag.total, "trend": tag.trend,
                    "by_year": [{"fiscal_year": y, "approvals": n} for y, n in tag.years],
                    "uscis_names": [n.strip() for n in tag.uscis_name.split(";") if n.strip()]},
            "message": f"{tag.detail}. List built {built}."}


TOOLS = (
    ToolSpec("h1b_history",
             "One company's H-1B history from the USCIS H-1B Employer Data Hub list: approvals per fiscal year, the "
             "trend (rising, steady, falling, new, stopped) and the USCIS employer names matched. Give the name or "
             "the id (merges followed; a near name is a question). A company the list does not hold has no H-1B on "
             "record. Read only.", H1bHistoryInput, h1b_history),
)
