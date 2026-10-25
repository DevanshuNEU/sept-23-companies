"""The app's clock: a function that returns now as canonical UTC text. Tests inject a fixed one."""
from collections.abc import Callable

from fastapi import Request

Clock = Callable[[], str]


def get_clock(request: Request) -> Clock:
    """The clock `create_app` was given (default `utcnow`)."""
    return request.app.state.clock
