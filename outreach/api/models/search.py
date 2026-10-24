"""Search: the index the palette loads once, and the two live lookups behind it."""
from outreach.api.models.base import Wire
from outreach.api.models.company import CompanyStatus


class SearchCompany(Wire):
    id: int
    name: str
    aliases: list[str]
    domain: str | None
    status: CompanyStatus
    status_line: str
    in_progress: bool


class SearchPerson(Wire):
    id: int
    company_id: int
    company_name: str
    name: str
    title: str
    email: str | None
    status_line: str


class SearchIndex(Wire):
    as_of: str
    companies: list[SearchCompany]
    people: list[SearchPerson]


class PostingHit(Wire):
    posting_id: int
    title: str
    company_id: int
    company_name: str
    open: bool
