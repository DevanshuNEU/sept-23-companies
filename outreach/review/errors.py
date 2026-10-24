"""The one error the review package raises on purpose."""
from typing import Literal

Code = Literal["not_found", "conflict", "not_allowed", "bad_request"]


class ReviewError(Exception):
    """A review action that did not happen. `message` is plain words saying what did not happen.

    The API maps `code` to its envelope code of the same name.
    """

    def __init__(self, code: Code, message: str) -> None:
        super().__init__(message)
        self.code: Code = code
        self.message = message
