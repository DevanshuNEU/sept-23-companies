"""The pulse: `Pulse` as in `web/src/api/types.ts`."""
from outreach.api.models.base import Wire


class Pulse(Wire):
    revision: int  # the newest event id: it only grows, and every MCP write appends an event
