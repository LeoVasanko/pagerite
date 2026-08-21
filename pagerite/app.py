"""FastAPI application: server-rendered content pages plus Vue assets.

Route ordering matters: our routes are defined before
``frontend.route(app, "/")`` is called, so they take priority over
the asset routes that fastapi-vue inserts at that position during ``load()``.
The content catch-all (``/{path:path}``) is defined last, so built
frontend assets still win over content slugs; anything unmatched falls
through to content (and 404 if no page exists there).

The site structure is a tree of Nodes (see data.py); URL paths resolve by
walking the tree (``resolve``), moves are slot detach/attach
(``find_slot``) with a fresh order key from the new siblings.
"""

import asyncio
import gzip
import ipaddress
import mimetypes
import os
import re
import shutil
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import format_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import blake3
import msgspec
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_vue import Frontend
from kanta import Kanta
from pydantic import BaseModel

from pagerite import analytics, seed, views
from pagerite.__main__ import DEVMODE
from pagerite.data import (
    Data,
    Node,
    append_order,
    find_slot,
    prettify,
    resolve,
    sorted_nodes,
)
from pagerite.markdown import has_h1, render, toggle_task

DB_PATH = os.getenv("PAGERITE_DB", "pagerite.kantadb")

# Visit analytics go to their own JSON file, not the kanta database.
ANALYTICS_PATH = Path(
    os.getenv("PAGERITE_ANALYTICS", DB_PATH.replace(".kantadb", "") + ".analytics.json")
)
analytics_store = analytics.Store(ANALYTICS_PATH)

# Live WebSocket clients for the analytics stream.
_analytics_ws_clients: set[WebSocket] = set()
_analytics_broadcast_task: asyncio.Task | None = None


# Repository root from this file's location (pagerite/app.py -> ..).
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _geoip_db_path() -> Path | None:
    """Find a DB-IP MMDB in the repo root, preferring an already-decompressed
    ``.mmdb`` over the matching ``.mmdb.gz``.  Returns None if none is present.
    """
    mmdb = sorted(_REPO_ROOT.glob("dbip-*.mmdb"))
    if mmdb:
        return mmdb[0]
    gz = sorted(_REPO_ROOT.glob("dbip-*.mmdb.gz"))
    if gz:
        return gz[0]
    return None


class GeoIP:
    """Lazy DB-IP MMDB reader.  Call ``_load()`` once at startup before
    concurrent requests arrive; ``country()`` is read-only and safe to call
    from ``asyncio.to_thread`` workers afterwards.
    """

    def __init__(self) -> None:
        self._reader: object | None = None

    def _decompress(self, source: Path, target: Path) -> None:
        if target.exists():
            return
        tmp = target.with_suffix(target.suffix + ".tmp")
        with gzip.open(source, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp, target)

    def _load(self) -> None:
        if self._reader is not None:
            return
        source = _geoip_db_path()
        if source is None:
            return
        if source.suffix == ".gz":
            target = source.with_suffix("")
            self._decompress(source, target)
            source = target
        try:
            import maxminddb

            self._reader = maxminddb.open_database(str(source))
        except Exception:
            pass

    def country(self, ip: str) -> str:
        """Two-letter ISO country code for ``ip``, or "" when unavailable."""
        if not ip or self._reader is None:
            return ""
        try:
            rec = self._reader.get(ip)
            if rec:
                return (rec.get("country") or {}).get("iso_code", "")
        except Exception:
            pass
        return ""

    def city(self, ip: str) -> str:
        """City name for ``ip``, or "" when unavailable."""
        if not ip or self._reader is None:
            return ""
        try:
            rec = self._reader.get(ip)
            if rec:
                return (rec.get("city") or {}).get("names", {}).get("en", "")
        except Exception:
            pass
        return ""


_geoip = GeoIP()


# Our own data root; kanta edits it in place, reads are plain attribute access.
data = Data()
kanta = Kanta(DB_PATH, data)

# Vue build served at the site root, no SPA catch-all (assets only). The
# build mirrors the URL space: hashed, immutable files live under
# /_assets/ (assetsDir: '_/assets'), the favicon at /favicon.ico.
BUILD_DIR = Path(__file__).with_name("frontend-build")
frontend = Frontend(BUILD_DIR, spa=False, cached="/_assets/")


def _hash_name(body: bytes, orig: str) -> str:
    """Content-addressed file name: blake3 hash prefix + original extension."""
    ext = "".join(c for c in Path(orig).suffix.lower() if c.isalnum() or c == ".")
    return blake3.blake3(body).hexdigest()[:12] + ext


def _store_seed_file(markdown: str, banner: str, orig: str, body: bytes) -> tuple[str, str]:
    """Store a seed file content-addressed and point references at /_f/."""
    name = _hash_name(body, orig)
    data.files.setdefault(name, body)
    markdown = markdown.replace(f"]({orig}", f"](/_f/{name}")
    banner = banner.replace(f'src="/{orig}"', f'src="/_f/{name}"')
    banner = banner.replace(f'src="{orig}"', f'src="/_f/{name}"')
    return markdown, banner


def _ensure(menu: dict[str, Node], path: str) -> Node:
    """Return the node at ``path``, creating it and any missing ancestors
    (content-less category labels) appended at the end of their level."""
    nodes = menu
    node = None
    for seg in path.split("/"):
        node = nodes.get(seg)
        if node is None:
            node = Node(title=prettify(seg), order=append_order(nodes))
            nodes[seg] = node
        nodes = node.children
    return node


def _remove_page_content(menu: dict[str, Node], path: str) -> None:
    """Delete a page's markdown content.

    A node with children becomes a content-less category label; a childless
    node is removed entirely. Does nothing if the path does not exist.
    """
    slot = find_slot(menu, path)
    if slot is None:
        return
    node = slot[0].get(slot[1])
    if node is None:
        return
    if node.children:
        node.content = None
        node.modified = datetime.now(UTC)
    else:
        del slot[0][slot[1]]


def _migrate_legacy() -> None:
    """Rebuild the legacy flat page store as a tree (one-time migration)."""
    if not data.pages:
        return
    with kanta.transaction("migrate pages to tree"):
        for path, page in data.pages.items():
            node = _ensure(data.menu, path)
            node.title = page.title
            node.content = page.markdown
            node.banner = page.banner
            node.published = page.published
            node.order = page.order
            node.created = page.created
            node.modified = page.modified
        data.pages.clear()
        data.version += 1


@kanta.bootstrap
def _seed(data: Data) -> None:
    """Write the demo pages on database creation (never on existing dbs)."""
    for path in seed.PAGES:
        title, markdown, files, banner, order, design = seed.PAGES[path]
        for orig, body in files.items():
            markdown, banner = _store_seed_file(markdown, banner, orig, body)
        node = _ensure(data.menu, path)
        node.title = title
        # Empty markdown means a pure category label (e.g. "showcase",
        # seeded only to carry a banner design): leave content as None so
        # the node renders the placeholder and nav points at its children.
        if markdown:
            node.content = markdown
        node.banner = banner
        node.banner_design = design
        node.order = order


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Open the database, migrate legacy content, load assets, load GeoIP."""
    await kanta.open()
    _migrate_legacy()
    await frontend.load()
    # Decompress/open the DB-IP MMDB once at startup.  Lookups are then
    # read-only and safe to run in background ``to_thread`` workers.
    await asyncio.to_thread(_geoip._load)
    analytics_store.subscribe(_schedule_analytics_broadcast)
    yield
    analytics_store.unsubscribe(_schedule_analytics_broadcast)
    await kanta.close()


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


@app.middleware("http")
async def _headers(request: Request, call_next) -> Response:
    """Replace uvicorn's default Server header with ours (no version)."""
    response = await call_next(request)
    response.headers["server"] = "pagerite"
    return response


class PageIn(BaseModel):
    """Payload for creating or replacing a page."""

    title: str
    markdown: str
    published: bool = True
    banner: str | None = None  # None keeps the existing banner


@app.get("/_api/pages")
async def list_pages() -> list[dict]:
    """The site tree for the structure editor (all nodes, drafts included).

    Nested by slug; each node carries its full path, menu order and flags.
    """

    def dump(nodes: dict[str, Node], prefix: str) -> list[dict]:
        out = []
        for slug, node in sorted_nodes(nodes):
            path = f"{prefix}/{slug}" if prefix else slug
            out.append({
                "slug": slug,
                "path": path,
                "title": node.title,
                "order": node.order,
                "published": node.published,
                "has_content": node.content is not None,
                "children": dump(node.children, path),
            })
        return out

    return dump(data.menu, "")


@app.put("/_api/pages/{path:path}", status_code=204)
async def save_page(path: str, page: PageIn) -> None:
    """Create or replace the page at a slug path ("" or "/" = front page).

    Missing ancestors are created as content-less category labels. Giving
    a category markdown turns it into a landing page. Empty markdown (after
    stripping) creates an empty page that renders with just its title —
    saving never deletes; use DELETE to remove a page (the page editor
    issues DELETE when you save empty text).
    """
    path = path.strip("/")
    _check_reserved(path)
    with kanta.transaction("save page", extra=path):
        node = _ensure(data.menu, path)
        node.title = page.title
        node.content = page.markdown
        node.published = page.published
        if page.banner is not None:
            node.banner = page.banner
        node.modified = datetime.now(UTC)
        data.version += 1


class StructureOp(BaseModel):
    """Rearrange the site tree: reorder, move/rename or retitle a node.

    `order` is a fresh fractional key computed client-side from the node's
    new siblings (a value halfway between them); all other items keep
    theirs. `move_to` is the full target path — the parent must exist and
    the new slug be free. Moves carry the whole subtree. The front page is
    just the top-level node with slug "": renaming it away leaves no front
    page ("/" then redirects to the first nav item), and any childless
    top-level node can take the empty slug to become the front page.
    """

    path: str
    order: float | None = None
    move_to: str | None = None
    title: str | None = None


@app.post("/_api/structure", status_code=204)
async def update_structure(op: StructureOp) -> None:
    """Apply one structure operation (see StructureOp)."""
    path = op.path.strip("/")
    chain = resolve(data.menu, path)
    if chain is None:
        raise HTTPException(404, "no such page")
    node = chain[-1]
    target = op.move_to.strip("/") if op.move_to is not None else None
    if target is not None and target != path:
        _check_reserved(target)
        if path and target.startswith(f"{path}/"):
            raise HTTPException(400, "cannot move a page under itself")
        slot = find_slot(data.menu, target)
        if slot is None:
            raise HTTPException(404, "target parent does not exist")
        tnodes, tslug = slot
        if tslug in tnodes:
            raise HTTPException(400, "target path exists")
        if not tslug and node.children:
            raise HTTPException(400, "the front page cannot have children")
    with kanta.transaction("update structure", extra=path):
        if op.title is not None:
            node.title = op.title
        if target is not None and target != path:
            snodes, sslug = find_slot(data.menu, path)
            del snodes[sslug]
            # A pure rename (same parent) keeps its position; only a move
            # to another level appends at the end (unless an order came
            # with the drop).
            same_level = path.rpartition("/")[0] == target.rpartition("/")[0]
            node.order = (
                op.order
                if op.order is not None
                else node.order if same_level else append_order(tnodes)
            )
            tnodes[tslug] = node
        elif op.order is not None:
            node.order = op.order
        node.modified = datetime.now(UTC)
        data.version += 1


@app.get("/_api/settings")
async def get_settings() -> dict:
    """Site-wide settings (brand, theme, custom CSS and favicon URL), plus
    the themes and banner designs available on disk for the selectors."""
    return {
        "brand": data.brand,
        "brand_html": data.brand_html,
        "theme": data.theme,
        "custom_css": data.custom_css,
        "favicon": f"/_f/{data.favicon}" if data.favicon else "",
        "themes": views._theme_names(),
        "banner_designs": views._banner_design_names(),
    }


class SettingsIn(BaseModel):
    """Payload for updating site-wide settings."""

    brand: str
    theme: str
    custom_css: str
    brand_html: str = ""


@app.put("/_api/settings", status_code=204)
async def put_settings(settings: SettingsIn) -> None:
    """Update site-wide settings; bumps the version so ETags invalidate."""
    with kanta.transaction("update settings"):
        data.brand = settings.brand
        data.brand_html = settings.brand_html
        data.theme = settings.theme
        data.custom_css = settings.custom_css
        data.version += 1


@app.put("/_api/settings/favicon")
async def put_favicon(request: Request) -> dict[str, str]:
    """Upload a favicon into the content-addressed store and activate it.

    Raw image body (ico/png/svg...); the stored name is a blake3 hash
    prefix + extension, and pages link it as <link rel="icon">. Returns
    {"path": "/_f/..."}.
    """
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty file")
    stored = _hash_name(body, request.headers.get("x-filename", "favicon.ico"))
    with kanta.transaction("upload favicon"):
        data.files[stored] = body
        data.favicon = stored
        data.version += 1
    return {"path": f"/_f/{stored}"}


@app.delete("/_api/settings/favicon", status_code=204)
async def delete_favicon() -> None:
    """Clear the custom favicon (back to the build's /favicon.ico).

    The blob stays in the content-addressed store; only the reference goes.
    """
    with kanta.transaction("clear favicon"):
        data.favicon = ""
        data.version += 1


class ToggleTaskIn(BaseModel):
    """Payload for toggling one task-list checkbox."""

    path: str
    index: int
    markdown: str | None = None


@app.post("/_api/toggle-task")
async def toggle_task_endpoint(body: ToggleTaskIn) -> dict[str, str]:
    """Toggle the Nth task-list checkbox in a page's Markdown source.

    If ``markdown`` is provided the source is left untouched and the toggled
    Markdown is returned (used while the page editor is open, so the live
    CodeMirror document can be updated). Otherwise the stored page at
    ``path`` is read, toggled, and saved.
    """
    path = body.path.strip("/")
    _check_reserved(path)
    if body.markdown is not None:
        new_markdown = toggle_task(body.markdown, body.index)
        if new_markdown is None:
            raise HTTPException(400, "invalid task index")
        return {"markdown": new_markdown}
    chain = resolve(data.menu, path)
    node = chain[-1] if chain else None
    if node is None or node.content is None:
        raise HTTPException(404, "no such page")
    new_markdown = toggle_task(node.content, body.index)
    if new_markdown is None:
        raise HTTPException(400, "invalid task index")
    with kanta.transaction("toggle task", extra=path):
        node.content = new_markdown
        node.modified = datetime.now(UTC)
        data.version += 1
    return {"markdown": new_markdown}


@app.put("/_api/files/{name}")
async def upload_file(name: str, request: Request) -> dict[str, str]:
    """Store an upload (image, video...) in the content-addressed store.

    The stored name is a blake3 hash prefix + the original extension,
    served immutable at "/_f/{name}"; returns {"path": "/_f/..."}.
    """
    if "/" in name or name in {".", ".."}:
        raise HTTPException(400, "bad file name")
    body = await request.body()
    stored = _hash_name(body, name)
    with kanta.transaction("upload file", extra=name):
        data.files[stored] = body
        data.version += 1
    return {"path": f"/_f/{stored}"}


@app.delete("/_api/files/{name}", status_code=204)
async def delete_file(name: str) -> None:
    """Remove a file from the content-addressed store (no refcounting:
    other pages referencing the same content will 404)."""
    if name not in data.files:
        raise HTTPException(404, "no such file")
    with kanta.transaction("delete file", extra=name):
        del data.files[name]
        data.version += 1


@app.get("/_themes/{name}/{filename}")
async def theme_file(name: str, filename: str, request: Request) -> Response:
    """Serve a theme/banner-design file from pagerite/themes/{name}/.

    Stylesheets plus any extra assets the CSS references (like summer's
    grass.svg). Read from disk on every request (etag by mtime+size):
    theme files are never built or content-hashed, so edits on disk show
    on the next page load, in prod as well as dev.
    """
    if (
        "/" in filename
        or filename.startswith(".")
        or "/" in name
        or name.startswith(".")
    ):
        raise HTTPException(404)
    path = views.THEMES / name / filename
    try:
        stat = path.stat()
    except FileNotFoundError:
        raise HTTPException(404) from None
    if not path.is_file():
        raise HTTPException(404)
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        path.read_bytes(),
        media_type=mime,
        headers={"etag": etag, "cache-control": "no-cache"},
    )


@app.get("/_f/{name}")
async def stored_file(name: str, request: Request) -> Response:
    """Serve a file from the content-addressed store (immutable: the name
    is its own hash, so cache forever)."""
    body = data.files.get(name)
    if body is None:
        raise HTTPException(404)
    if request.headers.get("if-none-match") == name:
        return Response(status_code=304)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return Response(
        body,
        media_type=mime,
        headers={"etag": name, "cache-control": "public, max-age=31536000, immutable"},
    )


@app.delete("/_api/pages/{path:path}", status_code=204)
async def delete_page(path: str) -> None:
    """Delete a node by slug path.

    A category (node with children) loses only its landing page and stays
    as a content-less label; a childless node is removed entirely.
    """
    path = path.strip("/")
    _check_reserved(path)
    slot = find_slot(data.menu, path)
    node = slot[0].get(slot[1]) if slot else None
    if node is None:
        raise HTTPException(404, "no such page")
    with kanta.transaction("delete page", extra=path):
        if node.children:
            node.content = None
            node.modified = datetime.now(UTC)
        else:
            del slot[0][slot[1]]
        data.version += 1


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _client_ip(request: Request) -> str:
    """Client IP: first X-Forwarded-For hop (we sit behind a proxy), else
    the direct peer."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")


@lru_cache(maxsize=4096)
def _cached_ptr(ip: str) -> str:
    """Reverse-DNS lookup with in-RAM LRU cache.  Returns the host name or ""."""
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_multicast or addr.is_link_local:
        return ""
    try:
        host, _, _ = socket.gethostbyaddr(ip)
    except socket.herror:
        return ""
    return host


async def _lookup_host(ip: str) -> str:
    """Async wrapper around ``_cached_ptr``; runs the blocking lookup in a thread."""
    return await asyncio.to_thread(_cached_ptr, ip)


async def _geoip_country(ip: str) -> str:
    """Async wrapper around the DB-IP MMDB lookup."""
    return await asyncio.to_thread(_geoip.country, ip)


async def _geoip_city(ip: str) -> str:
    """Async wrapper around the DB-IP MMDB city lookup."""
    return await asyncio.to_thread(_geoip.city, ip)


async def _enrich_visit(index: int, ip: str) -> None:
    """Run non-blocking reverse-DNS and geoip enrichment for a new visit."""
    if not ip:
        return
    host = await _lookup_host(ip)
    country = await _geoip_country(ip)
    city = await _geoip_city(ip)
    analytics_store.enrich_visit(index, host=host, country=country, city=city)


async def _broadcast_analytics() -> None:
    """Send the current analytics snapshot to every connected WS client."""
    if not _analytics_ws_clients:
        return
    payload = msgspec.json.encode(analytics_store.data).decode()
    closed = set()
    for ws in _analytics_ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            closed.add(ws)
    for ws in closed:
        _analytics_ws_clients.discard(ws)


async def _debounced_analytics_broadcast() -> None:
    """Wait briefly, then broadcast the latest snapshot once."""
    await asyncio.sleep(0.2)
    await _broadcast_analytics()


def _schedule_analytics_broadcast() -> None:
    """Schedule a single debounced broadcast, ignoring duplicate triggers."""
    global _analytics_broadcast_task
    if _analytics_broadcast_task is not None and not _analytics_broadcast_task.done():
        return
    _analytics_broadcast_task = asyncio.get_running_loop().create_task(
        _debounced_analytics_broadcast()
    )


class AnalyticsPing(BaseModel):
    """Navigation ping from pagerite.js (see docs/analytics.md)."""

    fr: str = ""
    to: str


@app.get("/_a", response_model=None)
async def analytics_page(request: Request) -> HTMLResponse:
    """Render the analytics viewer as a normal site page at /_a.

    The page itself is public, but the data stream (/_api/ws/analytics) stays
    admin-gated like the rest of /_api, so only authorized users see the
    statistics; others get the viewer with a "could not be loaded" message.
    """
    return HTMLResponse(
        views.render_analytics(
            data.menu, data.brand, data.custom_css, data.theme, data.favicon, data.brand_html
        ),
        headers={"cache-control": "no-cache"},
    )


@app.post("/_a", status_code=204)
async def analytics_ping(ping: AnalyticsPing, request: Request) -> None:
    """Record a navigation ping ({fr, to}); fire-and-forget, never fails.

    The reverse-DNS and DB-IP geoip lookups happen in a background task so
    the response is never delayed by slow DNS or the first MMDB decompress.
    """
    ip = _client_ip(request)
    index = analytics_store.ping(
        ping.fr,
        ping.to,
        ip,
        request.headers.get("user-agent", ""),
        request.headers.get("accept-language", ""),
    )
    if index is not None:
        asyncio.create_task(_enrich_visit(index, ip))


def _track_entry(path: str, request: Request) -> None:
    """Stash the referer/UTM tags and queue a pending crawler hit for the GET.

    Nothing is counted on the GET itself — the client's /_a ping starts the
    visit, so bots and admin browsing never register as visits.

    The devserver's health probe (``GET /?from=devserver.py`` from
    ``127.0.0.1``) is ignored: it is not real traffic and would otherwise be
    logged as a crawler hit.  The root-path and localhost checks prevent
    remote visitors from hiding traffic with the same query string.
    """
    if (
        path == ""
        and str(request.url.query) == "from=devserver.py"
        and _client_ip(request) == "127.0.0.1"
    ):
        return
    own_origin = f"https://{urlparse(str(request.base_url)).netloc}"
    analytics_store.track_entry(
        request.headers.get("referer", ""),
        own_origin,
        _client_ip(request),
        request.headers.get("user-agent", ""),
        "/" if path == "" else f"/{path}",
        str(request.url.query),
    )


def _http_date(dt: datetime) -> str:
    """RFC 7231 date for the Last-Modified header."""
    return format_datetime(dt.astimezone(UTC), usegmt=True)


def _is_reserved(path: str) -> bool:
    """Slug shape that content may never use: each segment must be lower-case
    ASCII letters, digits, hyphens and underscores (underscores may not be
    the first character), and dots are never allowed.
    """
    if path == "":
        return False
    return any(not _SLUG_RE.match(seg) for seg in path.split("/"))


def _is_trackable_path(path: str) -> bool:
    """Content URLs only: skip auth endpoints and reserved/machinery paths."""
    if not path:
        return True
    if path == "auth" or path.startswith("auth/"):
        return False
    return not _is_reserved(path)


def _check_reserved(path: str) -> None:
    """Reject paths that do not follow the slug charset."""
    if _is_reserved(path):
        raise HTTPException(
            400,
            'slugs may only use a-z, 0-9, "-" and "_" (not as the first character), and no dots',
        )


@app.websocket("/_api/ws/analytics")
async def analytics_websocket(ws: WebSocket) -> None:
    """Stream the analytics snapshot, then push updates as they happen.

    Admin-only via the /_api forward-auth gate, like every management
    endpoint. Powers the analytics viewer rendered at /_a.
    """
    await ws.accept()
    await ws.send_text(msgspec.json.encode(analytics_store.data).decode())
    _analytics_ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except Exception:
        pass
    finally:
        _analytics_ws_clients.discard(ws)


@app.websocket("/_api/ws/editor")
async def editor_ws(ws: WebSocket) -> None:
    """Editor session: open pages, render previews, save — over one socket.

    Stateless protocol (each message carries the path):
      <- {"type": "open", "path"}
      -> {"type": "doc", "path", "exists", "title", "markdown", "published",
          "banner", "banner_design"}
      <- {"type": "render", "path", "markdown"}
      -> {"type": "html", "path", "html"}
      <- {"type": "save", "path", "title"?, "markdown"?, "published"?,
          "banner"?, "banner_design"?, "move_from"?}   (absent fields keep
          their old values; move_from: rename/move a page, subtree included)
      -> {"type": "saved", "path"} | {"type": "error", "detail"}
    """
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            path = msg.get("path", "").strip("/")
            try:
                _check_reserved(path)
            except HTTPException:
                await ws.send_json({"type": "error", "detail": "reserved path"})
                continue
            match msg.get("type"):
                case "open":
                    chain = resolve(data.menu, path)
                    node = chain[-1] if chain else None
                    await ws.send_json({
                        "type": "doc",
                        "path": path,
                        "exists": node is not None,
                        "title": node.title if node else "",
                        "markdown": node.content if node and node.content is not None else "",
                        "published": node.published if node else True,
                        "banner": node.banner if node else "",
                        # Own banner design setting: null = inherit,
                        # "" = none, otherwise a design name.
                        "banner_design": node.banner_design if node else None,
                        # Which node's banner applies here ("" = front page,
                        # null = default artwork); the site editor shows it
                        # as the banner field's placeholder.
                        "banner_from": views.banner_source(data.menu, path),
                        # Which node's banner-design setting would apply on
                        # inherit ("" = front page, null = the active
                        # theme's default) and what design that resolves to.
                        "banner_design_from": (
                            src := views.banner_design_source(
                                data.menu, path, data.theme
                            )
                        ),
                        "banner_design_inherited": (
                            views.banner_design(data.menu, src, data.theme)
                            if src is not None
                            else views.theme_banner_design(data.theme)
                        ),
                    })
                case "render":
                    markdown = msg.get("markdown", "")
                    chain = resolve(data.menu, path)
                    node = chain[-1] if chain else None
                    await ws.send_json({
                        "type": "html",
                        "path": path,
                        "html": render(
                            markdown,
                            path,
                            node.created if node else None,
                            node.modified if node else None,
                        ),
                        "has_h1": has_h1(markdown),
                    })
                case "save":
                    move_from = (msg.get("move_from") or path).strip("/")
                    try:
                        _check_reserved(move_from)
                    except HTTPException:
                        await ws.send_json({"type": "error", "detail": "reserved path"})
                        continue
                    old_chain = resolve(data.menu, move_from)
                    old = old_chain[-1] if old_chain else None
                    if old is None and move_from != path:
                        move_from = path  # nothing to carry over; plain save
                    if move_from != path:
                        # Rename/move: detach the node (subtree included)
                        # and attach it at the new path. The target slug
                        # must be free and the front page childless.
                        if move_from and path.startswith(f"{move_from}/"):
                            await ws.send_json({
                                "type": "error",
                                "detail": "cannot move a page under itself",
                            })
                            continue
                        tslug = path.rpartition("/")[2]
                        if not tslug and old.children:
                            await ws.send_json({
                                "type": "error",
                                "detail": "the front page cannot have children",
                            })
                            continue
                        tchain = resolve(data.menu, path)
                        if tchain is not None:
                            await ws.send_json({
                                "type": "error",
                                "detail": "target path exists",
                            })
                            continue
                    with kanta.transaction("editor save", extra=path):
                        if move_from != path:
                            same_menu = (
                                move_from.rpartition("/")[0] == path.rpartition("/")[0]
                            )
                            snodes, sslug = find_slot(data.menu, move_from)
                            node = snodes.pop(sslug)
                            parent = path.rpartition("/")[0]
                            if parent:
                                _ensure(data.menu, parent)
                            tnodes, tslug = find_slot(data.menu, path)
                            node.order = (
                                node.order if same_menu else append_order(tnodes)
                            )
                            tnodes[tslug] = node
                        else:
                            node = old if old is not None else _ensure(data.menu, path)
                        if "markdown" in msg:
                            # Saving never deletes; empty markdown is an
                            # empty page. Deletion is an explicit choice by
                            # the page editor (REST DELETE).
                            node.content = msg["markdown"]
                        if "title" in msg:
                            node.title = msg["title"]
                        if "published" in msg:
                            node.published = bool(msg["published"])
                        if "banner" in msg:
                            node.banner = msg["banner"]
                        if "banner_design" in msg:
                            node.banner_design = msg["banner_design"]
                        node.modified = datetime.now(UTC)
                        data.version += 1
                    await ws.send_json({"type": "saved", "path": path})
    except WebSocketDisconnect:
        pass


@app.get("/")
async def front_page(request: Request) -> Response:
    """Render the front page (slug path "")."""
    return await show_page(request, "")


# Vue build asset routes are inserted at this position during load(): the
# build mirrors the URL space (/_assets/*, /favicon.ico at the root).
frontend.route(app, "/")


@app.get("/{path:path}", response_model=None)
async def show_page(request: Request, path: str) -> HTMLResponse | Response:
    """Render the content page at a slug path, or 404.

    A node without content is a category label: its URL renders a
    placeholder page (nav links point straight at its first child).
    """
    path = path.strip("/")
    if path and _is_reserved(path):
        # Invalid slug shape: not a content URL, let FastAPI return its
        # built-in 404 instead of rendering an editable article page.
        raise HTTPException(404)
    chain = resolve(data.menu, path)
    node = chain[-1] if chain else None
    if node is not None and node.published and node.content is not None:
        # no-cache forbids serving a stored page without revalidation
        # (browsers would otherwise cache heuristically and serve stale
        # pages, e.g. after a theme change). In-session speed instead comes
        # from pagerite.js's in-memory page cache (preload everything, never
        # fetch on navigation); the ETag just makes those one-time preload
        # fetches and any revalidation cheap.
        etag = f'"{path}@{node.modified.timestamp()}v{data.version}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)
        if _is_trackable_path(path):
            _track_entry(path, request)
        return HTMLResponse(
            views.render_page(data.menu, path, data.brand, data.custom_css, data.theme, data.favicon, data.brand_html, str(request.base_url).rstrip("/")),
            headers={
                "etag": etag,
                "last-modified": _http_date(node.modified),
                "cache-control": "no-cache",
            },
        )
    if node is not None and node.published and node.content is None:
        # Category label without a landing page: placeholder with the pen
        # to create it (404 — no page here, but the node is real).
        if _is_trackable_path(path):
            _track_entry(path, request)
        return HTMLResponse(
            views.render_category(data.menu, path, data.brand, data.custom_css, data.theme, data.favicon, data.brand_html),
            404,
            headers={
                "last-modified": _http_date(node.modified),
                "cache-control": "no-cache",
            },
        )
    if node is None and not path:
        # No front page (no top-level node with slug ""): "/" opens the
        # first item of the navigation instead.
        for slug, item in sorted_nodes(data.menu):
            if item.published:
                return RedirectResponse(f"/{slug}")
    if _is_trackable_path(path):
        _track_entry(path, request)
    return HTMLResponse(views.render_not_found(data.menu, path, data.brand, data.custom_css, data.theme, data.favicon, data.brand_html), 404)
