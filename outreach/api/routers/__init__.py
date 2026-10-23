"""One module per view. `outreach.api.app` includes each module's `router` under `/api`."""
from fastapi import Path

MAX_ID = 2**62  # a longer number cannot name a row, and SQLite would refuse it (an OverflowError, so a 500)


def row_id():
    """The bounds of every id in a path: `company_id: int = row_id()`. A new one each time: FastAPI keeps the name
    of the parameter it was first used for on the object, so one shared object would answer for the wrong field."""
    return Path(ge=1, le=MAX_ID)
