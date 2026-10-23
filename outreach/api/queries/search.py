"""Search: the index the palette loads, posting search (substring or initials) and company search.

A merged company is never listed on its own: it answers as its survivor, and lends its name to it as an alias.
"""
import re
import sqlite3

from outreach.api.models.search import PostingHit, SearchCompany, SearchIndex, SearchPerson
from outreach.api.queries import people as people_queries
from outreach.api.queries.company import (
    NAME_ALIAS_SOURCES, aliases_for, best_facts, merge_parents, status_line, survivor_of,
)
from outreach.api.queries.people import scope

HITS = 20
SOURCE_LIMIT = 200  # candidates read per source before merged companies fold into their survivors
STOP_WORDS = {"of", "the", "and", "a", "an", "at", "for", "in", "to"}


def _and(where: str, condition: str) -> str:
    return where + (" AND " if where else " WHERE ") + condition


def companies(conn: sqlite3.Connection, survivors: list[int] | None = None) -> list[SearchCompany]:
    """One `SearchCompany` per survivor, in the order given (every unmerged company by name when None)."""
    parents = merge_parents(conn)
    if survivors is None:
        survivors = [r[0] for r in conn.execute(
            "SELECT company_id FROM company WHERE merged_into IS NULL ORDER BY name COLLATE NOCASE, company_id")]
        ids = None
    else:
        wanted = set(survivors)
        ids = sorted(wanted | {c for c in parents if survivor_of(parents, c) in wanted})
    members: dict[int, list[int]] = {s: [s] for s in survivors}
    for c in parents:
        end = survivor_of(parents, c)
        if end in members:
            members[end].append(c)
    where, params = scope(ids, "company_id")
    rows = {r["company_id"]: r for r in conn.execute(f"SELECT * FROM company{where}", params)}
    domains: dict[int, list[sqlite3.Row]] = {}
    for r in conn.execute(f"SELECT * FROM company_fact{_and(where, 'fact = ' + repr('domain'))}", params):
        domains.setdefault(r["company_id"], []).append(r)
    alias_rows: dict[int, list[sqlite3.Row]] = {}
    for r in conn.execute(f"SELECT * FROM company_alias{where}", params):
        alias_rows.setdefault(r["company_id"], []).append(r)
    out = []
    for s in survivors:
        group = members[s]
        main = rows[s]
        domain = best_facts([d for c in group for d in domains.get(c, [])]).get("domain")
        out.append(SearchCompany(
            id=s, name=main["name"],
            aliases=aliases_for({c: rows[c] for c in group}, s, [a for c in group for a in alias_rows.get(c, [])]),
            domain=domain["value"] if domain else None, status=main["status"],
            status_line=status_line(main["status"], main["close_reason"], main["paused_at"], main["held_by_touch"]),
            in_progress=main["status"] == "working",
        ))
    return out


def index(conn: sqlite3.Connection, now: str) -> SearchIndex:
    # TODO(#6): cap this at the touched companies plus the top scored (the ranking package supplies the scores);
    # companies the cap leaves out are found by `find_companies`.
    everyone = companies(conn)
    parents = merge_parents(conn)
    rows = {r["company_id"]: r for r in conn.execute("SELECT * FROM company")}
    # A person on a merged duplicate is judged against the survivor's pause and hold.
    data = people_queries.load(conn, None, bodies=False)
    lines = people_queries.lines(data, {c: rows[survivor_of(parents, c)] for c in rows})
    names = {c.id: c.name for c in everyone}
    people = []
    for p in data.persons:
        company_id = survivor_of(parents, p["company_id"])
        if company_id not in names:  # a merge cycle has no survivor: the person keeps the company they are on
            company_id = p["company_id"]
            names[company_id] = rows[company_id]["name"]
        people.append(SearchPerson(
            id=p["person_id"], company_id=company_id, company_name=names[company_id], name=p["name"], title=p["title"],
            email=p["email"], status_line=lines[p["person_id"]].status_line,
        ))
    return SearchIndex(as_of=now, companies=everyone, people=people)


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def find_companies(conn: sqlite3.Connection, q: str) -> list[SearchCompany]:
    """Up to 20 companies whose name, alias or domain contains `q` (any status), names first."""
    q = q.strip().lower()
    if not q:
        return []
    pattern = _like(q)
    prefix = _like(q)[1:]  # the same pattern without the leading %
    alias_sources = ",".join(repr(s) for s in NAME_ALIAS_SOURCES)

    def quality(column: str) -> str:
        """0 for an exact match, 1 for a prefix, 2 for a substring."""
        return f"CASE WHEN lower({column}) = ? THEN 0 WHEN lower({column}) LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END"

    def best_first(column: str) -> str:
        """Exact match, then prefix, then substring, so the LIMIT never cuts the best match."""
        return f"ORDER BY {quality(column)}, {column} LIMIT {SOURCE_LIMIT}"

    order = (q, prefix)
    hits = (
        conn.execute(
            f"SELECT company_id, {quality('name')} AS q FROM company WHERE name LIKE ? ESCAPE '\\' {best_first('name')}",
            (*order, pattern, *order)).fetchall(),
        conn.execute(
            f"SELECT company_id, {quality('alias')} AS q FROM company_alias WHERE source IN ({alias_sources}) "
            f"AND alias LIKE ? ESCAPE '\\' {best_first('alias')}", (*order, pattern, *order)).fetchall(),
        conn.execute(
            f"SELECT company_id, {quality('value')} AS q FROM evidence WHERE fact = 'domain' "
            f"AND value LIKE ? ESCAPE '\\' {best_first('value')}", (*order, pattern, *order)).fetchall(),
    )
    parents = merge_parents(conn)
    rank: dict[int, tuple[int, int]] = {}  # (how well it matched, which source: name, alias, domain)
    for tier, found in enumerate(hits):
        for company_id, match in found:
            end = survivor_of(parents, company_id)
            rank[end] = min(rank.get(end, (match, tier)), (match, tier))
    if not rank:
        return []
    where, params = scope(rank, "company_id")
    names = {r[0]: r[1].lower() for r in conn.execute(f"SELECT company_id, name FROM company{where}", params)}
    ordered = sorted(rank, key=lambda c: (rank[c], names[c], c))[:HITS]
    return companies(conn, ordered)


def _matches(title: str, q: str) -> bool:
    """A posting matches by substring, or by the first letters of its words ("mts" finds "Member of Technical Staff")."""
    t = title.lower()
    if q in t:
        return True
    initials = "".join(w[0] for w in re.split(r"[^a-z0-9]+", t) if w and w not in STOP_WORDS)
    return len(q) >= 2 and q in initials


def find_postings(conn: sqlite3.Connection, q: str) -> list[PostingHit]:
    """Up to 20 postings, open ones first and newest first. A merged company's postings answer as its survivor."""
    q = q.strip().lower()
    if not q:
        return []
    found = []
    cursor = conn.execute(
        "SELECT posting_id, title, company_id, closed_at FROM posting ORDER BY closed_at IS NOT NULL, posting_id DESC")
    for row in cursor:
        if _matches(row["title"], q):
            found.append(row)
            if len(found) == HITS:
                break
    cursor.close()
    if not found:
        return []
    parents = merge_parents(conn)
    ends = {r["company_id"]: survivor_of(parents, r["company_id"]) for r in found}
    where, params = scope(set(ends.values()), "company_id")
    names = {r[0]: r[1] for r in conn.execute(f"SELECT company_id, name FROM company{where}", params)}
    return [
        PostingHit(posting_id=r["posting_id"], title=r["title"], company_id=ends[r["company_id"]],
                   company_name=names[ends[r["company_id"]]], open=r["closed_at"] is None)
        for r in found
    ]
