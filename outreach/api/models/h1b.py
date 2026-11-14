"""The H-1B tag on a job row and an Outbox row (#252): a plain fact read from the H-1B list, never a sort or a hide."""
from outreach.api.models.base import Wire


class H1bTagWire(Wire):
    label: str  # "H-1B 2,436, rising" or "No H-1B on record"
    detail: str  # approvals by fiscal year, for the tag's hover text
    trend: str  # rising, falling, steady, new, stopped, one year or none
