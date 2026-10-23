"""The app factory. Routers are found by scanning `outreach.api.routers`, so a view is one new module."""
import asyncio
import contextlib
import importlib
import pkgutil
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.routing import APIRoute

import outreach.api.routers
from outreach.api import errors, openapi, sweeper
from outreach.api.config import load_config
from outreach.api.guards import GuardMiddleware
from outreach.api.idempotency import prune_at_startup
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        prune_at_startup(settings, app.state.clock())  # old idempotency keys, once per start
        if settings.sweeper:  # tests turn it off and call sweeper.sweep_quiet themselves
            app.state.sweeper_tasks.append(
                asyncio.create_task(sweeper.run_forever(settings, app.state.clock, app.state.config)))
        try:
            yield
        finally:  # cancelled on shutdown; a pass already in its thread finishes (its write is one transaction)
            for task in app.state.sweeper_tasks:
                task.cancel()
            for task in app.state.sweeper_tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        lifespan=lifespan,
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
    app.state.config = load_config()
    app.state.sweeper_tasks = []

    api = APIRouter(prefix="/api")

    @api.get("/ping", response_model=Ping)
    def ping() -> Ping:
        return Ping(ok=True)

    for module in sorted(pkgutil.iter_modules(outreach.api.routers.__path__), key=lambda m: m.name):
        api.include_router(importlib.import_module(f"outreach.api.routers.{module.name}").router)
    app.include_router(api, responses=openapi.ERROR_RESPONSES)
    errors.install(app)
    openapi.install(app)
    app.add_middleware(GZipMiddleware, minimum_size=1024)  # responses over 1 KB; the guard below stays outermost
    # Added last so it is the outermost middleware: a refused request never reaches the body parser or a router.
    app.add_middleware(GuardMiddleware, settings=settings)
    return app
