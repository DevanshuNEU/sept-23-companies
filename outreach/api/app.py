"""The app factory. Routers are found by scanning `outreach.api.routers`, so a view is one new module."""
import importlib
import pkgutil

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

import outreach.api.routers
from outreach.api import errors, openapi
from outreach.api.guards import GuardMiddleware
from outreach.api.models.ping import Ping
from outreach.api.settings import Settings, settings_from_env
from outreach.store.db import guard
from outreach.store.timefmt import utcnow


def create_app(settings: Settings | None = None, *, clock=None, mx_resolvers=None) -> FastAPI:
    """Build the app.

    `clock` returns the current time as canonical UTC text (tests inject a fixed one). `mx_resolvers` replaces the
    mail-server lookups (tests inject fakes). The store is not opened here: each request opens its own connection.
    Like every entry point, this refuses the live store anywhere but the live checkout.
    """
    settings = settings or settings_from_env()
    guard(settings.db_path)
    app = FastAPI(
        title="Outreach API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        generate_unique_id_function=lambda route: route.name,
    )
    app.state.settings = settings
    app.state.clock = clock or utcnow
    app.state.mx_resolvers = mx_resolvers

    api = APIRouter(prefix="/api")

    @api.get("/ping", response_model=Ping)
    def ping() -> Ping:
        return Ping(ok=True)

    for module in sorted(pkgutil.iter_modules(outreach.api.routers.__path__), key=lambda m: m.name):
        api.include_router(importlib.import_module(f"outreach.api.routers.{module.name}").router)
    app.include_router(api, responses=openapi.ERROR_RESPONSES)
    errors.install(app)
    openapi.install(app)
    # Added last so it is the outermost middleware: a refused request never reaches the body parser or a router.
    app.add_middleware(GuardMiddleware, settings=settings)
    return app
