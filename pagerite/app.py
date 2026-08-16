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

import mimetypes
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import blake3
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_vue import Frontend
from kanta import Kanta
from pydantic import BaseModel

from pagerite import seed, views
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

DB_PATH = os.getenv("PAGERITE_DB", "pagerite.kanta")

# Our own data root; kanta edits it in place, reads are plain attribute access.
data = Data()
kanta = Kanta(DB_PATH, data)

# Vue build served at the site root, no SPA catch-all (assets only). The
# build mirrors the URL space: hashed, immutable files live under
# /_/assets/ (assetsDir: '_/assets'), the favicon at /favicon.ico.
BUILD_DIR = Path(__file__).with_name("frontend-build")
frontend = Frontend(BUILD_DIR, spa=False, cached="/_/assets/")


def _hash_name(body: bytes, orig: str) -> str:
    """Content-addressed file name: blake3 hash prefix + original extension."""
    ext = "".join(c for c in Path(orig).suffix.lower() if c.isalnum() or c == ".")
    return blake3.blake3(body).hexdigest()[:12] + ext


def _store_seed_file(markdown: str, banner: str, orig: str, body: bytes) -> tuple[str, str]:
    """Store a seed file content-addressed and point references at /_/f/."""
    name = _hash_name(body, orig)
    data.files.setdefault(name, body)
    markdown = markdown.replace(f"]({orig}", f"](/_/f/{name}")
    banner = banner.replace(f'src="/{orig}"', f'src="/_/f/{name}"')
    banner = banner.replace(f'src="{orig}"', f'src="/_/f/{name}"')
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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Open the database, migrate/seed content, load assets."""
    await kanta.open()
    _migrate_legacy()
    missing = [p for p in seed.PAGES if resolve(data.menu, p) is None]
    if missing:
        with kanta.transaction("seed missing pages"):
            for path in missing:
                title, markdown, files, banner, order = seed.PAGES[path]
                for orig, body in files.items():
                    markdown, banner = _store_seed_file(markdown, banner, orig, body)
                node = _ensure(data.menu, path)
                node.title = title
                node.content = markdown
                node.banner = banner
                node.order = order
    await frontend.load()
    yield
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


class PageIn(BaseModel):
    """Payload for creating or replacing a page."""

    title: str
    markdown: str
    published: bool = True
    banner: str | None = None  # None keeps the existing banner


@app.get("/_/api/health")
async def health_check() -> dict[str, str]:
    """Return backend status for health monitoring."""
    return {"status": "ok"}


@app.get("/_/api/pages")
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


@app.put("/_/api/pages/{path:path}", status_code=204)
async def save_page(path: str, page: PageIn) -> None:
    """Create or replace the page at a slug path ("" or "/" = front page).

    Missing ancestors are created as content-less category labels. Giving
    a category markdown turns it into a landing page. An empty markdown
    string (after stripping) deletes the page instead.
    """
    path = path.strip("/")
    _check_reserved(path)
    with kanta.transaction("save page", extra=path):
        if page.markdown.strip() == "":
            _remove_page_content(data.menu, path)
        else:
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


@app.post("/_/api/structure", status_code=204)
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


@app.get("/_/api/settings")
async def get_settings() -> dict[str, str]:
    """Site-wide settings (the brand text)."""
    return {"brand": data.brand}


class SettingsIn(BaseModel):
    """Payload for updating site-wide settings."""

    brand: str


@app.put("/_/api/settings", status_code=204)
async def put_settings(settings: SettingsIn) -> None:
    """Update site-wide settings; bumps the version so ETags invalidate."""
    with kanta.transaction("update settings"):
        data.brand = settings.brand
        data.version += 1


class ToggleTaskIn(BaseModel):
    """Payload for toggling one task-list checkbox."""

    path: str
    index: int
    markdown: str | None = None


@app.post("/_/api/toggle-task")
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


@app.put("/_/api/files/{name}")
async def upload_file(name: str, request: Request) -> dict[str, str]:
    """Store an upload (image, video...) in the content-addressed store.

    The stored name is a blake3 hash prefix + the original extension,
    served immutable at "/_/f/{name}"; returns {"path": "/_/f/..."}.
    """
    if "/" in name or name in {".", ".."}:
        raise HTTPException(400, "bad file name")
    body = await request.body()
    stored = _hash_name(body, name)
    with kanta.transaction("upload file", extra=name):
        data.files[stored] = body
        data.version += 1
    return {"path": f"/_/f/{stored}"}


@app.delete("/_/api/files/{name}", status_code=204)
async def delete_file(name: str) -> None:
    """Remove a file from the content-addressed store (no refcounting:
    other pages referencing the same content will 404)."""
    if name not in data.files:
        raise HTTPException(404, "no such file")
    with kanta.transaction("delete file", extra=name):
        del data.files[name]
        data.version += 1


@app.get("/_/f/{name}")
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


@app.delete("/_/api/pages/{path:path}", status_code=204)
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


def _is_reserved(path: str) -> bool:
    """Slug shape that content may never use: each segment must be lower-case
    ASCII letters, digits, hyphens and underscores (underscores may not be
    the first character), and dots are never allowed.
    """
    if path == "":
        return False
    return any(not _SLUG_RE.match(seg) for seg in path.split("/"))


def _check_reserved(path: str) -> None:
    """Reject paths that do not follow the slug charset."""
    if _is_reserved(path):
        raise HTTPException(
            400,
            'slugs may only use a-z, 0-9, "-" and "_" (not as the first character), and no dots',
        )


@app.websocket("/_/api/ws/editor")
async def editor_ws(ws: WebSocket) -> None:
    """Editor session: open pages, render previews, save — over one socket.

    Stateless protocol (each message carries the path):
      <- {"type": "open", "path"}
      -> {"type": "doc", "path", "exists", "title", "markdown", "published",
          "banner"}
      <- {"type": "render", "path", "markdown"}
      -> {"type": "html", "path", "html"}
      <- {"type": "save", "path", "title"?, "markdown"?, "published"?,
          "banner"?, "move_from"?}   (absent fields keep their old values;
          move_from: rename/move a page, subtree included)
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
                        # Which node's banner applies here ("" = front page,
                        # null = default artwork); the site editor shows it
                        # as the banner field's placeholder.
                        "banner_from": views.banner_source(data.menu, path),
                    })
                case "render":
                    markdown = msg.get("markdown", "")
                    await ws.send_json({
                        "type": "html",
                        "path": path,
                        "html": render(markdown, path),
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
                        removed = False
                        if "markdown" in msg:
                            if msg["markdown"].strip() == "":
                                _remove_page_content(data.menu, path)
                                removed = True
                            else:
                                node.content = msg["markdown"]
                        if not removed:
                            if "title" in msg:
                                node.title = msg["title"]
                            if "published" in msg:
                                node.published = bool(msg["published"])
                            if "banner" in msg:
                                node.banner = msg["banner"]
                            node.modified = datetime.now(UTC)
                        data.version += 1
                    await ws.send_json({"type": "saved", "path": path})
    except WebSocketDisconnect:
        pass


@app.get("/_/admin", response_class=HTMLResponse)
async def admin() -> HTMLResponse:
    """Serve the editor app shell (Vue mounts into #app)."""
    return HTMLResponse(views.render_editor())


@app.get("/")
async def front_page(request: Request) -> Response:
    """Render the front page (slug path "")."""
    return await show_page(request, "")


# Vue build asset routes are inserted at this position during load(): the
# build mirrors the URL space (/_/assets/*, /favicon.ico at the root).
frontend.route(app, "/")


@app.get("/{path:path}", response_model=None)
async def show_page(request: Request, path: str) -> HTMLResponse | Response:
    """Render the content page at a slug path, or 404.

    A node without content is a category label: its URL renders a
    placeholder page (nav links point straight at its first child).
    """
    path = path.strip("/")
    if path and _is_reserved(path):
        # Reserved slug shape: never content — no tree lookup.
        return HTMLResponse(views.render_not_found(data.menu, path, data.brand), 404)
    chain = resolve(data.menu, path)
    node = chain[-1] if chain else None
    if node is not None and node.published and node.content is not None:
        # ETag on content + render version; clients revalidate cheaply,
        # which keeps prefetched pages warm and current.
        etag = f'"{path}@{node.modified.timestamp()}v{data.version}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)
        return HTMLResponse(
            views.render_page(data.menu, path, data.brand),
            headers={"etag": etag},
        )
    if node is not None and node.published and node.content is None:
        # Category label without a landing page: placeholder with the pen
        # to create it (404 — no page here, but the node is real).
        return HTMLResponse(views.render_category(data.menu, path, data.brand), 404)
    if node is None and not path:
        # No front page (no top-level node with slug ""): "/" opens the
        # first item of the navigation instead.
        for slug, item in sorted_nodes(data.menu):
            if item.published:
                return RedirectResponse(f"/{slug}")
    return HTMLResponse(views.render_not_found(data.menu, path, data.brand), 404)
