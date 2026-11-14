"""The H-1B tag of a company for the job list and the Outbox (#252). Reads one cached file, never the store."""
from outreach.api.models.h1b import H1bTagWire
from outreach.h1b.lookup import tag_for

__all__ = ["h1b_wire"]


def h1b_wire(name: str | None) -> H1bTagWire | None:
    """None when no H-1B list is built, so a row never claims "No H-1B on record" without a list to say it."""
    tag = tag_for(name)
    return None if tag is None else H1bTagWire(label=tag.label, detail=tag.detail, trend=tag.trend)
