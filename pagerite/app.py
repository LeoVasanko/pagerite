"""FastAPI application assembly: server-rendered content pages plus Vue assets.

The routes live in specialized modules, included below as APIRouters:

- ``pagerite.state`` — shared core, no routes: site constants, the kanta
  database, the analytics store, the fastapi-vue frontend, the render
  cache, the translator dispatcher, and the database bootstrap hooks.
- ``pagerite.files`` — the content-addressed file store and its routes
  (``/_api/files``, ``/_f/``, ``/_themes/``, ``/_fonts/``, favicon).
- ``pagerite.api`` — the editor REST API and WebSocket sessions
  (``/_api/*``, ``/_translate/{clientkey}``).
- ``pagerite.tracking`` — visit analytics (``/_ws``, ``/_api/ws/analytics``,
  the ``/_a`` viewer page).
- ``pagerite.pages`` — the public content pages: ``/``, ``/sitemap.xml``,
  ``/robots.txt`` and the ``/{path:path}`` catch-all.

Route ordering matters: our own routers are included before
``frontend.route(app, "/")`` is called. That call only records the current
route-table length; the actual asset routes are spliced in at that position
later, when ``frontend.load()`` runs inside the lifespan — so they take
priority over anything registered after this point but never shadow our
own routes. The content catch-all (``/{path:path}``) is included last, so
built frontend assets still win over content slugs; anything unmatched
falls through to content (and 404 if no page exists there).

The site structure is a tree of Nodes (see data.py); URL paths resolve by
walking the tree (``resolve``), moves are slot detach/attach
(``find_slot``) with a fresh order key from the new siblings.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi_vue import Frontend
from starlette.types import ASGIApp, Receive, Scope, Send

from pagerite import api, files, pages, tracking
from pagerite.__main__ import DEVMODE
from pagerite.files import file_store
from pagerite.state import analytics_store, config, kanta

logger = logging.getLogger(__name__)

# Vue build served at the site root, no SPA catch-all (assets only). The
# build mirrors the URL space: hashed, immutable files live under
# /_assets/ (assetsDir: '_/assets').
frontend = Frontend(
    Path(__file__).with_name("frontend-build"), spa=False, cached="/_assets/"
)


class _AccessLogExtraMiddleware:
    """Fill the ``log_extra`` slot of fastapi_vue's access log.

    Everything under ``/_api`` is gated by the SSO forward-auth, which names
    the authenticated user in the ``remote-user`` header; put that user on
    the access-log line, for plain requests and WebSocket open/close alike.
    The scope dict is shared with the outer AccessLogMiddleware, which reads
    the slot back at response/accept/close time.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and scope["path"].startswith("/_api"):
            headers = dict(scope["headers"])
            user = headers.get(b"remote-user", b"").decode("latin-1")
            if user:
                scope.setdefault("state", {})["log_extra"] = user
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator:
    """Open the database (migrations run inside kanta.open), load assets, load GeoIP."""
    async with kanta:
        await asyncio.to_thread(file_store.load)
        await frontend.load()
        # --dbip: update the DB-IP database first, then decompress/open the
        # MMDB once.  Lookups are then read-only and safe to run in
        # background ``to_thread`` workers.
        if config.dbip:
            await asyncio.to_thread(tracking._download_dbip)
        await asyncio.to_thread(tracking._geoip._load)
        analytics_store.subscribe(tracking._schedule_analytics_broadcast)
        # Backfill favicons for external sites already in the recorded data.
        tracking._schedule_favicon_fetch()
        yield
        analytics_store.unsubscribe(tracking._schedule_analytics_broadcast)


# docs_url/openapi_url disabled: /docs belongs to our content, and the API
# is not meant to be browsable by the public anyway.
app = FastAPI(
    title="Pagerite",
    debug=DEVMODE,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(_AccessLogExtraMiddleware)


@app.middleware("http")
async def _headers(request: Request, call_next) -> Response:
    """Replace uvicorn's default Server header with ours (no version)."""
    response = await call_next(request)
    response.headers["server"] = "pagerite"
    return response


# Our own routes first: the editor API and translator socket, the analytics
# machinery, and the file store/user assets.
app.include_router(api.router)
app.include_router(tracking.router)
app.include_router(files.router)

# Vue build asset routes are inserted at this position during load(): the
# build mirrors the URL space (/_assets/*).
frontend.route(app, "/")

# The content catch-all goes last: built assets win over content slugs,
# anything unmatched falls through to content (and 404).
app.include_router(pages.router)
