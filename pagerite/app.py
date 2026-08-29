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
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from email.utils import format_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

import blake3
import httpx
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import RedirectResponse, Response
from fastapi_vue import Frontend
from kanta import Kanta
from mediapreview import dispatch
from pydantic import BaseModel
from zstandard import ZstdCompressor

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
from pagerite.markdown import render, toggle_task

# Site identity: the hostname comes from the CLI (first positional argument,
# exported as PAGERITE_HOSTNAME) and names the per-site data directory
# ``<hostname>/{content.kantadb, analytics.json, files}`` under the cwd.
HOSTNAME = os.getenv("PAGERITE_HOSTNAME", "localhost")
SITE_DIR = Path(HOSTNAME)
#: Public origin of the site, used for absolute social/canonical/sitemap
#: URLs. Localhost serves varying ports, so it falls back to the request's
#: own base URL instead.
SITE_URL = f"https://{HOSTNAME}" if HOSTNAME != "localhost" else ""

DB_PATH = os.getenv("PAGERITE_DB", str(SITE_DIR / "content.kantadb"))

# Visit analytics go to their own JSON file, not the kanta database.
ANALYTICS_PATH = Path(os.getenv("PAGERITE_ANALYTICS", str(SITE_DIR / "analytics.json")))
analytics_store = analytics.Store(ANALYTICS_PATH)

# Content-addressed file store (uploads, seed assets, fetched favicons):
# files on disk under hash-prefixed names, cached in RAM, served at /_f/.
FILES_DIR = Path(os.getenv("PAGERITE_FILES", str(SITE_DIR / "files")))

# Uploaded images are thumbnailed to this size and recompressed to AVIF
# (primary), with WebP and JPEG fallbacks re-encoded from the AVIF at
# somewhat lower quality (similar or smaller file size); the untouched
# original is kept alongside as ``<hash>.orig<ext>`` (never served).
IMAGE_MAXSIZE = 1920
IMAGE_QUALITY = 60
IMAGE_WEBP_QUALITY = 50
IMAGE_JPG_QUALITY = 55

# Favicons get the same derivatives but thumbnailed much smaller — 192px
# is plenty (browsers scale down for the 16x16 tab icon themselves).
FAVICON_MAXSIZE = 192

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
        """City name for ``ip``, or "" when unavailable.

        GeoIP sometimes appends district names in parentheses (e.g.
        "Berlin (Bezirk Tempelhof-Schöneberg)"); those are stripped before
        the value is stored.
        """
        if not ip or self._reader is None:
            return ""
        try:
            rec = self._reader.get(ip)
            if rec:
                city = (rec.get("city") or {}).get("names", {}).get("en", "")
                if city:
                    city = re.sub(r"\s*\([^)]*\)", "", city).strip()
                return city
        except Exception:
            pass
        return ""


_geoip = GeoIP()


# Our own data root; kanta edits it in place, reads are plain attribute access.
data = Data()
kanta = Kanta(DB_PATH, data, migrations="pagerite.migrations")

# Vue build served at the site root, no SPA catch-all (assets only). The
# build mirrors the URL space: hashed, immutable files live under
# /_assets/ (assetsDir: '_/assets'), the favicon at /favicon.ico.
BUILD_DIR = Path(__file__).with_name("frontend-build")
frontend = Frontend(BUILD_DIR, spa=False, cached="/_assets/")


def _ext(orig: str) -> str:
    """Sanitized lowercase extension (with dot) of an original file name."""
    return "".join(c for c in Path(orig).suffix.lower() if c.isalnum() or c == ".")


def _hash_name(body: bytes, orig: str) -> str:
    """Content-addressed file name: blake3 hash prefix + original extension."""
    return blake3.blake3(body).hexdigest()[:12] + _ext(orig)


def _store_seed_file(markdown: str, banner: str, orig: str, body: bytes) -> tuple[str, str]:
    """Store a seed file content-addressed and point references at /_f/.

    Images get the same AVIF/WebP/JPEG derivatives as uploads and are
    linked extension-less; other content is stored as-is with its
    extension."""
    digest = blake3.blake3(body).hexdigest()[:12]
    derivatives = None if _ext(orig) == ".gif" else _image_derivatives(body, _ext(orig))
    if derivatives is None:
        file_store.put(digest + _ext(orig), body)
        name = digest + _ext(orig)
    else:
        file_store.put(f"{digest}.svg" if _ext(orig) == ".svg" else f"{digest}.orig{_ext(orig)}", body)
        for fmt, variant in derivatives.items():
            file_store.put(f"{digest}.{fmt}", variant)
        name = digest
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
    """Open the database (migrations run inside kanta.open), load assets, load GeoIP."""
    await kanta.open()
    await asyncio.to_thread(file_store.load)
    await frontend.load()
    # Decompress/open the DB-IP MMDB once at startup.  Lookups are then
    # read-only and safe to run in background ``to_thread`` workers.
    await asyncio.to_thread(_geoip._load)
    analytics_store.subscribe(_schedule_analytics_broadcast)
    # Backfill favicons for external sites already in the recorded data.
    _schedule_favicon_fetch()
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


# Dynamic HTML is compressed per request at level 9 (static assets are
# already pre-compressed by fastapi-vue's Frontend).
_zstd = ZstdCompressor(9)


class FileStore:
    """Content-addressed files on disk, fully cached in RAM.

    Every file is kept in RAM uncompressed and zstd-compressed (the
    compressed copy only when it actually shrinks the body), so ``/_f``
    serves both encodings without touching disk or re-compressing.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        #: name -> (uncompressed body, zstd body or None)
        self._cache: dict[str, tuple[bytes, bytes | None]] = {}

    @staticmethod
    def _entry(body: bytes) -> tuple[bytes, bytes | None]:
        compressed = _zstd.compress(body)
        return body, compressed if len(compressed) < len(body) else None

    def load(self) -> None:
        """Read every stored file into the RAM cache (startup)."""
        try:
            entries = sorted(self.path.iterdir())
        except FileNotFoundError:
            return
        for f in entries:
            if f.is_file() and not f.name.startswith("."):
                self._cache.setdefault(f.name, self._entry(f.read_bytes()))

    def get(self, name: str) -> tuple[bytes, bytes | None] | None:
        return self._cache.get(name)

    def put(self, name: str, body: bytes) -> None:
        """Store ``body`` under ``name`` on disk and in the RAM cache."""
        if name in self._cache:
            return
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / name).write_bytes(body)
        self._cache[name] = self._entry(body)

    def delete(self, name: str) -> None:
        """Delete a file plus its derivatives/original counterparts, if any.

        An image upload is stored as a group sharing the hash prefix
        (``<hash>.orig.<ext>`` + ``<hash>.avif/.webp/.jpg``); deleting any
        of the names removes them all.
        """
        stem = name.partition(".")[0]
        for key in [k for k in self._cache if k.partition(".")[0] == stem]:
            self._cache.pop(key, None)
            with suppress(FileNotFoundError):
                (self.path / key).unlink()

    def __contains__(self, name: str) -> bool:
        return name in self._cache


file_store = FileStore(FILES_DIR)


def _render_html(kind: str, path: str, base_url: str) -> str:
    """Render one of the generated pages (see _html_response)."""
    if kind == "page":
        return views.render_page(data.menu, path, data.brand, data.custom_css, data.theme, data.favicon, data.brand_html, base_url, transition=data.transition)
    if kind == "category":
        return views.render_category(data.menu, path, data.brand, data.custom_css, data.theme, data.favicon, data.brand_html, transition=data.transition)
    if kind == "not-found":
        return views.render_not_found(data.menu, path, data.brand, data.custom_css, data.theme, data.favicon, data.brand_html, transition=data.transition)
    return views.render_analytics(data.menu, data.brand, data.custom_css, data.theme, data.favicon, data.brand_html, transition=data.transition)


# Render generation: bumped (and the body cache cleared) by every
# content/settings write, so page ETags and cached copies invalidate when
# navigation-affecting changes happen. In-memory only — not database state.
_render_gen = 0


def _invalidate_pages() -> None:
    """Drop cached page bodies and bump the render generation (ETags)."""
    global _render_gen
    _render_gen += 1
    _cached_body.cache_clear()


@lru_cache(maxsize=128)
def _cached_body(kind: str, path: str, base_url: str, zstd: bool) -> bytes:
    """Rendered page body; cleared by _invalidate_pages on any
    content/settings change. base_url feeds the social meta URLs and zstd
    selects the stored encoding (both variants are cached rather than
    re-compressed).
    """
    body = _render_html(kind, path, base_url).encode()
    return _zstd.compress(body) if zstd else body


def _html_response(
    request: Request,
    kind: str,
    path: str,
    status_code: int = 200,
    headers: dict | None = None,
    etag: bool = False,
) -> Response:
    """Response for a generated page, zstd-compressed when the client
    accepts it (no gzip fallback).

    Done per handler rather than in middleware so that Frontend's
    already-compressed asset responses are never touched. The ETag stays
    identical across encodings (revalidation compares it before
    compression); ``vary: accept-encoding`` keeps caches from mixing the
    representations. In dev the cache is bypassed so theme/design edits on
    disk apply immediately.

    ``etag=True`` derives the validator from a blake3 hash of the
    (uncompressed) body — for pages like /_a that have no Node whose
    modified timestamp could serve as one — and answers matching
    if-none-match revalidations with a 304.
    """
    zstd = "zstd" in request.headers.get("accept-encoding", "")
    # Absolute social/canonical URLs use the site's public origin; on
    # localhost (varying ports) fall back to the request's own base URL.
    base_url = SITE_URL or str(request.base_url).rstrip("/")
    if DEVMODE:
        identity = _render_html(kind, path, base_url).encode()
        body = _zstd.compress(identity) if zstd else identity
    else:
        identity = _cached_body(kind, path, base_url, False)
        body = _cached_body(kind, path, base_url, True) if zstd else identity
    h = dict(headers or {})
    if zstd:
        h["vary"] = "accept-encoding"
    if etag:
        tag = f'"{blake3.blake3(identity).hexdigest()[:32]}"'
        h["etag"] = tag
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=h)
    if zstd:
        h["content-encoding"] = "zstd"
    return Response(body, status_code, h, media_type="text/html")


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
        _invalidate_pages()


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
        _invalidate_pages()


@app.get("/_api/settings")
async def get_settings() -> dict:
    """Site-wide settings (brand, theme, custom CSS and favicon URL), plus
    the themes, banner designs and user fonts available on disk for the
    selectors."""
    return {
        "brand": data.brand,
        "brand_html": data.brand_html,
        "theme": data.theme,
        "custom_css": data.custom_css,
        "favicon": f"/_f/{data.favicon}" if data.favicon else "",
        "themes": views._theme_info(),
        "banner_designs": views._banner_design_names(),
        "fonts": views._user_fonts(),
        "transition": data.transition,
        "transitions": views._transition_names(),
    }


class SettingsIn(BaseModel):
    """Payload for updating site-wide settings."""

    brand: str
    theme: str
    custom_css: str
    brand_html: str = ""
    transition: str = "cube"


@app.put("/_api/settings", status_code=204)
async def put_settings(settings: SettingsIn) -> None:
    """Update site-wide settings; invalidates cached pages and ETags."""
    with kanta.transaction("update settings"):
        data.brand = settings.brand
        data.brand_html = settings.brand_html
        data.theme = settings.theme
        data.custom_css = settings.custom_css
        data.transition = settings.transition
        _invalidate_pages()


@app.put("/_api/settings/favicon")
async def put_favicon(request: Request) -> dict[str, str]:
    """Upload a favicon into the content-addressed store and activate it.

    Raw image body (ico/png/svg...). Decodable images are thumbnailed to
    FAVICON_MAXSIZE (192px — browsers scale down from there themselves)
    and stored as AVIF/WebP/JPEG derivatives linked extension-less; SVG
    originals also stay servable under their ``.svg`` name. Undecodable
    bodies are stored as-is. Pages link it as <link rel="icon">. Returns
    {"path": "/_f/..."}.
    """
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty file")
    ext = _ext(request.headers.get("x-filename", "favicon.ico"))
    digest = blake3.blake3(body).hexdigest()[:12]
    derivatives = await asyncio.to_thread(_image_derivatives, body, ext, FAVICON_MAXSIZE)
    if derivatives is None:  # undecodable (e.g. some .ico): store as-is
        stored = digest + ext
        file_store.put(stored, body)
    else:
        stored = digest
        file_store.put(f"{digest}.svg" if ext == ".svg" else f"{digest}.orig{ext}", body)
        for fmt, variant in derivatives.items():
            file_store.put(f"{digest}.{fmt}", variant)
    with kanta.transaction("upload favicon"):
        data.favicon = stored
        _invalidate_pages()
    return {"path": f"/_f/{stored}"}


@app.delete("/_api/settings/favicon", status_code=204)
async def delete_favicon() -> None:
    """Clear the custom favicon (back to the build's /favicon.ico).

    The blob stays in the content-addressed store; only the reference goes.
    """
    with kanta.transaction("clear favicon"):
        data.favicon = ""
        _invalidate_pages()


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
        _invalidate_pages()
    return {"markdown": new_markdown}


def _to_avif(body: bytes, ext: str, maxsize: int = IMAGE_MAXSIZE) -> bytes | None:
    """Recompress an image body to a thumbnailed AVIF via mediapreview's
    dispatch (pyvips for common formats, ffmpeg for HEIC/HEIF/AVIF), or
    None if the body is not a decodable image (stored as-is by the caller).
    Dispatch needs a real file for format routing, so the body goes
    through a temp file.
    """
    with tempfile.NamedTemporaryFile(suffix=ext) as tmp:
        tmp.write(body)
        tmp.flush()
        try:
            avif, _resp = dispatch(
                Path(tmp.name),
                quality=IMAGE_QUALITY,
                maxsize=maxsize,
                maxzoom=1,
            )
        except Exception:
            return None
        return avif


def _svg_to_png(body: bytes, maxsize: int) -> bytes | None:
    """Rasterize an SVG to PNG via pyvips, scaled so the long side is
    ``maxsize`` — SVGs often carry no meaningful intrinsic resolution, so
    we rasterize at full image size rather than the tiny nominal one."""
    import pyvips

    try:
        img = pyvips.Image.new_from_buffer(body, "")
        scale = maxsize / max(img.width, img.height) if img.width and img.height else maxsize
        if scale != 1:
            img = pyvips.Image.new_from_buffer(body, "", scale=scale)
        return img.write_to_buffer(".png")
    except pyvips.Error:
        return None


def _avif_to_format(avif: bytes, suffix: str, quality: int) -> bytes:
    """Re-encode the AVIF derivative into a fallback format (WebP/JPEG)
    via pyvips. JPEG has no alpha, so it is flattened onto white;
    ``strip`` keeps metadata (EXIF) out of the fallbacks."""
    import pyvips

    img = pyvips.Image.new_from_buffer(avif, "")
    if suffix == ".jpg" and img.hasalpha():
        img = img.flatten(background=[255, 255, 255])
    return img.write_to_buffer(suffix, Q=quality, strip=True)


def _image_derivatives(body: bytes, ext: str, maxsize: int = IMAGE_MAXSIZE) -> dict[str, bytes] | None:
    """The served variants of an uploaded image: ``avif`` (primary,
    thumbnailed to ``maxsize``) plus ``webp`` and ``jpg`` fallbacks
    re-encoded from it. SVGs are rasterized first (they are vector, so
    the raster replaces nothing — the .svg itself stays servable).
    Returns None for non-decodable content (stored as-is by the caller).
    """
    if ext == ".svg":
        png = _svg_to_png(body, maxsize)
        if png is None:
            return None
        body, ext = png, ".png"
    avif = _to_avif(body, ext, maxsize)
    if avif is None:
        return None
    return {
        "avif": avif,
        "webp": _avif_to_format(avif, ".webp", IMAGE_WEBP_QUALITY),
        "jpg": _avif_to_format(avif, ".jpg", IMAGE_JPG_QUALITY),
    }


@app.put("/_api/files/{name}")
async def upload_file(name: str, request: Request) -> dict[str, str]:
    """Store an upload (image, video...) in the content-addressed store.

    The stored name is a blake3 hash prefix + the original extension,
    served immutable at "/_f/{name}"; returns {"path": "/_f/..."}.

    Raster images and SVGs are recompressed (SVGs rasterized) into AVIF
    (primary) plus WebP and JPEG fallbacks: the original goes to
    ``<hash>.orig<ext>`` (kept for reprocessing, never served — it may
    carry EXIF data; SVG originals stay servable as ``<hash>.svg`` since
    vector carries no EXIF) and pages link the bare ``/_f/<hash>``, the
    server picking the format from the request's Accept header. GIFs are
    stored as-is (animation would be lost), as is other non-decodable
    content.
    """
    if "/" in name or name in {".", ".."}:
        raise HTTPException(400, "bad file name")
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty file")
    ext = _ext(name)
    digest = blake3.blake3(body).hexdigest()[:12]
    derivatives = (
        None
        if ext == ".gif"
        else await asyncio.to_thread(_image_derivatives, body, ext)
    )
    if derivatives is None:  # not a decodable image: store the body as-is
        stored = digest + ext
        file_store.put(stored, body)
        return {"path": f"/_f/{stored}"}
    file_store.put(f"{digest}.svg" if ext == ".svg" else f"{digest}.orig{ext}", body)
    for fmt, variant in derivatives.items():
        file_store.put(f"{digest}.{fmt}", variant)
    return {"path": f"/_f/{digest}"}


@app.delete("/_api/files/{name}", status_code=204)
async def delete_file(name: str) -> None:
    """Remove a file from the content-addressed store (no refcounting:
    other pages referencing the same content will 404)."""
    if name not in file_store:
        raise HTTPException(404, "no such file")
    file_store.delete(name)


async def _serve_user_file(path: Path | None, request: Request) -> Response:
    """Serve a user-asset file resolved on disk, with mtime etag.

    Read from disk on every request (etag by mtime+size): user assets are
    never built or content-hashed, so edits on disk show on the next page
    load, in prod as well as dev.
    """
    if path is None:
        raise HTTPException(404)
    stat = path.stat()
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Response(
        path.read_bytes(),
        media_type=mime,
        headers={"etag": etag, "cache-control": "no-cache"},
    )


@app.get("/_themes/{name}/{filename}")
async def theme_file(name: str, filename: str, request: Request) -> Response:
    """Serve a theme/banner-design file, resolved across views.THEME_DIRS.

    Stylesheets plus any extra assets the CSS references (like summer's
    grass.svg).
    """
    return await _serve_user_file(views.theme_file(name, filename), request)


@app.get("/_fonts/{name}/{filename}")
async def user_font_file(name: str, filename: str, request: Request) -> Response:
    """Serve a user font file, resolved across views.FONT_DIRS.

    The folder's font.css (@font-face rules + --font-{name} stack variable)
    is linked on every page; the woff2 files it references come from here.
    """
    return await _serve_user_file(views.font_file(name, filename), request)


@app.get("/_f/{name}")
async def stored_file(name: str, request: Request) -> Response:
    """Serve a file from the content-addressed store (immutable: the name
    is its own hash, so cache forever).  Bodies are served from the RAM
    cache, zstd-compressed when the client accepts it and compression
    actually shrank the file.

    A bare ``/_f/{hash}`` (no extension, how pages link uploaded images)
    content-negotiates between the stored derivatives: a format is served
    only when the Accept header lists it explicitly — ``image/avif`` →
    AVIF, ``image/webp`` → WebP, anything else (including ``image/*`` and
    ``*/*``) → JPEG. An explicit extension pins the format. ``.orig.``
    originals are internal (they may carry EXIF data) and never served."""
    if ".orig." in name:
        raise HTTPException(404)
    etag = name
    vary = ""
    entry = file_store.get(name)
    if entry is None and "." not in name:
        # Extension-less image link: negotiate avif/webp/jpg by Accept.
        vary = "accept"
        accept = request.headers.get("accept", "")
        if "image/avif" in accept:
            order = ("avif", "webp", "jpg")
        elif "image/webp" in accept:
            order = ("webp", "jpg", "avif")
        else:
            order = ("jpg", "webp", "avif")
        for ext in order:
            etag = f"{name}.{ext}"
            entry = file_store.get(etag)
            if entry is not None:
                break
    if entry is None:
        raise HTTPException(404)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    body, compressed = entry
    headers = {"etag": etag, "cache-control": "public, max-age=31536000, immutable"}
    if compressed is not None and "zstd" in request.headers.get("accept-encoding", ""):
        headers["content-encoding"] = "zstd"
        vary = f"{vary}, accept-encoding".lstrip(", ")
        body = compressed
    if vary:
        headers["vary"] = vary
    mime = mimetypes.guess_type(etag)[0] or "application/octet-stream"
    return Response(body, media_type=mime, headers=headers)


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
        _invalidate_pages()


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _client_ip(request: Request) -> str:
    """Client IP: first X-Forwarded-For hop (we sit behind a proxy), else
    the direct peer."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")


def _query_suffix(request: Request) -> str:
    """The request's query string as a "?..." suffix, or "" when absent."""
    query = str(request.url.query)
    return f"?{query}" if query else ""


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


async def _enrich_client(client_hash: bytes) -> None:
    """Run non-blocking reverse-DNS and geoip enrichment for a client."""
    client = analytics_store.data.clients.get(client_hash)
    if not client or not client.ip:
        return
    host = await _lookup_host(client.ip)
    country = await _geoip_country(client.ip)
    city = await _geoip_city(client.ip)
    analytics_store.enrich_client(client_hash, host=host, country=country, city=city)


def _schedule_client_enrichment(client_hashes: list[bytes]) -> None:
    """Start background host/geoip enrichment for the given client hashes."""
    for client_hash in client_hashes:
        asyncio.create_task(_enrich_client(client_hash))


#: Icon MIME -> file extension for the stored favicon name.  The extension
#: reflects the actual content, not the /favicon.ico request path.
_FAVICON_EXT = {
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
}

_FAVICON_MAX_BYTES = 65536

#: Origins with a fetch task currently in flight.
_favicon_in_flight: set[str] = set()


async def _fetch_favicon(origin: str) -> None:
    """Fetch ``{origin}/favicon.ico`` and store it content-hashed on disk.

    The result (icon file name, or "" for a miss) is recorded in the
    analytics store; misses are retried after analytics._FAVICON_RETRY.
    Never raises: analytics must not break page serving.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8) as client:
            r = await client.get(f"{origin}/favicon.ico")
        body = r.content
        if not (200 <= r.status_code < 300) or not body or len(body) > _FAVICON_MAX_BYTES:
            analytics_store.record_favicon(origin)
            return
        mime = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if not mime.startswith("image/"):
            # Served without an image type: sniff SVG, else assume ICO.
            if b"<svg" in body[:1024]:
                mime = "image/svg+xml"
            elif mime in ("", "application/octet-stream", "text/plain"):
                mime = "image/x-icon"
            else:
                analytics_store.record_favicon(origin)
                return
        ext = _FAVICON_EXT.get(mime, ".ico")
        name = _hash_name(body, f"favicon{ext}")
        file_store.put(name, body)
        analytics_store.record_favicon(origin, name)
    except (httpx.HTTPError, OSError):
        analytics_store.record_favicon(origin)
    finally:
        _favicon_in_flight.discard(origin)


def _schedule_favicon_fetch() -> None:
    """Start background favicon fetches for origins that need one."""
    for origin in analytics_store.favicon_origins_needed():
        if origin in _favicon_in_flight:
            continue
        _favicon_in_flight.add(origin)
        asyncio.create_task(_fetch_favicon(origin))


async def _broadcast_analytics() -> None:
    """Send the current analytics snapshot to every connected WS client."""
    if not _analytics_ws_clients:
        return
    payload = analytics_store.display_json()
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


@app.get("/_a", response_model=None)
async def analytics_page(request: Request) -> Response:
    """Render the analytics viewer as a normal site page at /_a.

    The page itself is public, but the data stream (/_api/ws/analytics) stays
    admin-gated like the rest of /_api, so only authorized users see the
    statistics; others get the viewer with a "could not be loaded" message.
    """
    return _html_response(
        request,
        "analytics",
        "",
        headers={"cache-control": "no-cache"},
        etag=True,
    )


@app.post("/_a", status_code=204)
async def analytics_ping(
    request: Request,
    fr: str = Query(""),
    to: str | None = Query(None),
    hide: int = Query(0),
    read: int = Query(0),
) -> None:
    """Record a navigation ping (?fr=&to=&hide=&read=); fire-and-forget.

    The initial page-load ping carries only ``to``: the entry is attributed
    to the referer/UTM tags stashed by the document GET (see _track_entry),
    which JS cannot see once the page has loaded.

    The reverse-DNS and DB-IP geoip lookups happen in a background task so
    the response is never delayed by slow DNS or the first MMDB decompress.
    """
    ip = _client_ip(request)
    visit_index, flushed_clients = analytics_store.ping(
        fr,
        to,
        ip,
        request.headers.get("user-agent", ""),
        request.headers.get("accept-language", ""),
        hide=bool(hide),
        read=read,
    )
    if visit_index is not None:
        visit = analytics_store.data.visits[visit_index]
        asyncio.create_task(_enrich_client(visit.client))
    _schedule_client_enrichment(flushed_clients)
    _schedule_favicon_fetch()


def _track_entry(path: str, request: Request, *, status: int = 200) -> list[bytes]:
    """Stash the referer/UTM tags and queue a pending crawler hit for the GET.

    Nothing is counted on the GET itself — the client's /_a ping starts the
    visit, so bots never register as visits (JS-running crawlers ping too,
    but the ping handler ignores known bot UAs).  (Admin clients ping too,
    but with hide=1, which flags their visit hidden: it is recorded but
    excluded from all statistics and from the crawler list.)

    The devserver's health probe (``GET /?from=devserver.py`` from
    ``127.0.0.1``) is ignored: it is not real traffic and would otherwise be
    logged as a crawler hit.  The root-path and localhost checks prevent
    remote visitors from hiding traffic with the same query string.

    Returns the client hashes of any pending crawler hits flushed to persistent
    storage, so callers can schedule async geoip and reverse-DNS enrichment.
    """
    if request.headers.get("x-pagerite-preload"):
        # Idle-time page-cache warm-up by pagerite.js, not a page view: the
        # ping sent when the user actually navigates does the counting.
        # (Forging the header only hides a GET from the crawler stats; the
        # path-based abuse classification is unaffected.)
        return []
    if (
        path == ""
        and str(request.url.query) == "from=devserver.py"
        and _client_ip(request) == "127.0.0.1"
    ):
        return []
    own_origin = SITE_URL or f"https://{urlparse(str(request.base_url)).netloc}"
    full_path = f"{request.url.path}{_query_suffix(request)}"
    return analytics_store.track_entry(
        request.headers.get("referer", ""),
        own_origin,
        _client_ip(request),
        request.headers.get("user-agent", ""),
        full_path,
        request.headers.get("accept-language", ""),
        status=status,
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
    await ws.send_text(analytics_store.display_json())
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
                    rendered = render(
                        markdown,
                        path,
                        node.created if node else None,
                        node.modified if node else None,
                        # The title is injected as h1 when the markdown has
                        # none; the editor's title field edits live-preview.
                        title=msg.get("title") or (node.title if node else ""),
                    )
                    await ws.send_json({
                        "type": "html",
                        "path": path,
                        "html": rendered.html,
                        # Column-layout flag: the preview toggles the
                        # article's .multicol class and swaps in the
                        # segmented (.colseg/.cols) article html.
                        "multicol": rendered.multicol,
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
                        _invalidate_pages()
                    await ws.send_json({"type": "saved", "path": path})
    except WebSocketDisconnect:
        pass


@app.get("/")
async def front_page(request: Request) -> Response:
    """Render the front page (slug path "")."""
    return await show_page(request, "")


@app.get("/sitemap.xml")
async def sitemap(request: Request) -> Response:
    """Dynamically generate a sitemap of all published article pages."""
    base = SITE_URL or str(request.base_url).rstrip("/")
    entries: list[tuple[str, datetime, int]] = []

    def walk(
        nodes: dict[str, Node], prefix: str, parent_has_content: bool = True
    ) -> None:
        first_content_slug = next(
            (
                slug
                for slug, node in sorted_nodes(nodes)
                if node.published and node.content is not None
            ),
            None,
        )
        for slug, node in sorted_nodes(nodes):
            path = f"{prefix}/{slug}" if prefix else slug
            depth = path.count("/") if path else 0
            if (
                not parent_has_content
                and slug == first_content_slug
                and node.published
                and node.content is not None
                and depth > 0
            ):
                depth -= 1
            if node.published and node.content is not None:
                entries.append((path, node.modified, depth))
            if node.children:
                walk(node.children, path, node.content is not None)

    walk(data.menu, "")

    def priority(depth: int) -> float:
        return max(0.1, 1.0 - depth * 0.2)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, modified, depth in entries:
        loc = xml_escape(f"{base}/{path}" if path else base)
        lastmod = (
            modified.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        lines.append(
            f"  <url>"
            f"<loc>{loc}</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            f"<priority>{priority(depth):.1f}</priority>"
            f"</url>"
        )
    lines.append("</urlset>")

    return Response(
        "\n".join(lines),
        media_type="application/xml",
        headers={"cache-control": "no-cache"},
    )


@app.get("/robots.txt")
async def robots_txt(request: Request) -> Response:
    """Allow all crawling and point crawlers at the sitemap."""
    base = SITE_URL or str(request.base_url).rstrip("/")
    body = f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"
    return Response(
        body,
        media_type="text/plain",
        headers={"cache-control": "no-cache"},
    )


# Vue build asset routes are inserted at this position during load(): the
# build mirrors the URL space (/_assets/*, /favicon.ico at the root).
frontend.route(app, "/")


@app.get("/{path:path}", response_model=None)
async def show_page(request: Request, path: str) -> Response:
    """Render the content page at a slug path, or 404.

    A node without content is a category label: its URL renders a
    placeholder page (nav links point straight at its first child).
    """
    path = path.strip("/")
    ua = request.headers.get("user-agent", "")
    accept_language = request.headers.get("accept-language", "")
    if path and _is_reserved(path):
        # Invalid slug shape: not a content URL, let FastAPI return its
        # built-in 404 instead of rendering an editable article page.
        # Scanner telltales (dotpaths like /.env, *.php) classify the IP
        # as abuse in analytics.
        client_hash = analytics_store.track_404(
            _client_ip(request),
            ua,
            f"/{path}{_query_suffix(request)}",
            accept_language,
        )
        asyncio.create_task(_enrich_client(client_hash))
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
        etag = f'"{path}@{node.modified.timestamp()}g{_render_gen}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)
        if _is_trackable_path(path):
            flushed = _track_entry(path, request)
            _schedule_client_enrichment(flushed)
        return _html_response(
            request,
            "page",
            path,
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
            flushed = _track_entry(path, request, status=404)
            _schedule_client_enrichment(flushed)
        return _html_response(
            request,
            "category",
            path,
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
        client_hash = analytics_store.track_404(
            _client_ip(request),
            ua,
            f"/{path}{_query_suffix(request)}",
            accept_language,
        )
        asyncio.create_task(_enrich_client(client_hash))
        flushed = _track_entry(path, request, status=404)
        _schedule_client_enrichment(flushed)
    return _html_response(request, "not-found", path, 404)
