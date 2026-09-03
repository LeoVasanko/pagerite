"""Editor REST API and WebSocket sessions.

The management endpoints behind the SSO forward-auth gate: the site tree
(``/_api/pages``), structure operations (``/_api/structure``), site-wide
settings (``/_api/settings``), task-list toggles (``/_api/toggle-task``),
the translations refresh (``/_api/translations``), the editor session
socket (``/_api/ws/editor``), and the translator service channel
(``/_translate/{clientkey}`` — deliberately NOT under ``/_api``: the
server-generated key in the path is the access control).
"""

import logging
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel

from pagerite import i18n, views
from pagerite.chunks import store_chunks
from pagerite.data import (
    Node,
    append_order,
    find_slot,
    node_markdown,
    resolve,
    sorted_nodes,
)
from pagerite.markdown import render, toggle_task
from pagerite.state import (
    _check_reserved,
    _ensure,
    _invalidate_pages,
    _remove_page,
    data,
    dispatcher,
    kanta,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class PageIn(BaseModel):
    """Payload for creating or replacing a page."""

    title: str
    markdown: str
    published: bool = True
    banner: str | None = None  # None keeps the existing banner


@router.get("/_api/pages")
async def list_pages(lang: str | None = None) -> list[dict]:
    """The site tree for the structure editor (all nodes, drafts included).

    Nested by slug; each node carries its full path, menu order, flags and
    language settings (``language`` is the node's own primary-language
    setting, "" = inherit; ``primary`` is the resolved effective one).
    With a ``?lang=`` translation, titles come out in that language where a
    translation exists (``translated`` flags it — true trivially for rows
    whose primary language IS the selected one; other rows fall back to
    the original title, dimmed) — the structure itself (slugs, order,
    hierarchy) is language-independent.
    """
    tag = i18n.base_tag(lang or "")
    titles = i18n.title_map(data, tag) if tag else {}

    def dump(nodes: dict[str, Node], prefix: str, inherited: str) -> list[dict]:
        out = []
        for slug, node in sorted_nodes(nodes):
            path = f"{prefix}/{slug}" if prefix else slug
            primary = node.language or inherited
            out.append(
                {
                    "slug": slug,
                    "path": path,
                    "title": titles.get(path) or node.title,
                    "translated": path in titles or (bool(tag) and primary == tag),
                    "order": node.order,
                    "published": node.published,
                    "has_content": node.chunks is not None,
                    "language": node.language,
                    "primary": primary,
                    "children": dump(node.children, path, primary),
                }
            )
        return out

    return dump(data.menu, "", i18n.ORIGINAL_LANGUAGE)


@router.put("/_api/pages/{path:path}", status_code=204)
async def save_page(
    path: str, page: PageIn, request: Request, lang: str | None = None
) -> None:
    """Create or replace the page at a slug path ("" or "/" = front page).

    Missing ancestors are created as content-less category labels. Giving
    a category markdown turns it into a landing page. Empty markdown (after
    stripping) creates an empty page that renders with just its title —
    saving never deletes; use DELETE to remove a page (the page editor
    issues DELETE when you save empty text).

    With a ``?lang=`` query (a translation, not the primary language) the
    save is a translated-view edit (docs/localization.md): the markdown is
    diffed against the currently served hybrid and the minimal diff is
    appended as a Patch under ``patches[f"{path}:{lang}"]`` — node.chunks
    and the original-language fields (title, published, banner) stay
    untouched.
    """
    path = path.strip("/")
    _check_reserved(path)
    lang = i18n.base_tag(lang or "")
    if lang and lang != i18n.primary_lang(data.menu, path):
        chain = resolve(data.menu, path)
        node = chain[-1] if chain else None
        if node is None or node.chunks is None:
            raise HTTPException(404, "no such page")
        with kanta.transaction(
            f"page:{lang}", user=request.headers.get("remote-user"), extra=path
        ):
            # Patches alone make the translated version exist.
            if i18n.add_patch(data, node, path, lang, page.markdown):
                _invalidate_pages()
        return
    with kanta.transaction("page", user=request.headers.get("remote-user"), extra=path):
        node = _ensure(data.menu, path)
        node.title = page.title
        node.chunks = store_chunks(data.chunks, page.markdown)
        node.published = page.published
        if page.banner is not None:
            node.banner = page.banner
        node.modified = datetime.now(UTC)
        _invalidate_pages()


@router.delete("/_api/pages/{path:path}", status_code=204)
async def delete_page(path: str, request: Request) -> None:
    """Delete a node by slug path.

    A category (node with children) loses only its landing page and stays
    as a content-less label; a childless node is removed entirely.
    """
    path = path.strip("/")
    _check_reserved(path)
    with kanta.transaction(
        "page:delete", user=request.headers.get("remote-user"), extra=path
    ):
        if not _remove_page(data.menu, path):
            raise HTTPException(404, "no such page")
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

    With `lang` (a translation, not the node's primary language) a `title`
    edit writes a per-language title fragment instead of the original — the
    same storage as machine title translations (docs/localization.md);
    sending the original's text removes the override. Structural fields are
    not combinable with a translated title edit.

    `language` sets the node's primary language (a BCP-47 base tag; "" =
    inherit from the nearest ancestor, the front page last, site default
    "en" final — Node.language), inherited by the whole subtree.
    """

    path: str
    order: float | None = None
    move_to: str | None = None
    title: str | None = None
    lang: str | None = None
    language: str | None = None


@router.post("/_api/structure", status_code=204)
async def update_structure(op: StructureOp, request: Request) -> None:
    """Apply one structure operation (see StructureOp)."""
    path = op.path.strip("/")
    chain = resolve(data.menu, path)
    if chain is None:
        raise HTTPException(404, "no such page")
    node = chain[-1]
    lang = i18n.base_tag(op.lang or "")
    if op.language is not None:
        # Primary-language setting (inherited by the subtree): reselects
        # what "the original" means for the node — its language is part of
        # every render, so a change invalidates everywhere.
        language = i18n.base_tag(op.language)
        with kanta.transaction(
            "page:language", user=request.headers.get("remote-user"), extra=path
        ):
            if language != node.language:
                node.language = language
                _invalidate_pages()
        return
    if op.title is not None and lang and lang != i18n.primary_lang(data.menu, path):
        # Translated title (i18n.set_title_translation): original title,
        # slugs and hierarchy stay untouched.
        with kanta.transaction(
            f"page:{lang}:title", user=request.headers.get("remote-user"), extra=path
        ):
            if i18n.set_title_translation(data, node, lang, op.title):
                _invalidate_pages()
        return
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
    # One structure call can combine a title set, a move/rename and a
    # reorder; the action names the most significant of them.
    action = (
        "page:slug"
        if target is not None and target != path
        else "page:title"
        if op.title is not None
        else "structure:reorder"
    )
    with kanta.transaction(action, user=request.headers.get("remote-user"), extra=path):
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
                else node.order
                if same_level
                else append_order(tnodes)
            )
            tnodes[tslug] = node
        elif op.order is not None:
            node.order = op.order
        node.modified = datetime.now(UTC)
        _invalidate_pages()


@router.get("/_api/settings")
async def get_settings() -> dict:
    """Site-wide settings (brand, theme, custom CSS and favicon URL), plus
    the themes, banner designs and user fonts available on disk for the
    selectors, the translator service keys and the wanted translation
    languages (for the /_translate socket)."""
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
        "translate_keys": data.translate_keys,
        # The site default primary language: the front page's resolved
        # setting (every page may override it, inherited down the tree).
        "primary_lang": i18n.primary_lang(data.menu, ""),
        "translate_langs": sorted(data.translate_langs),
    }


class SettingsIn(BaseModel):
    """Payload for updating site-wide settings."""

    brand: str
    theme: str
    custom_css: str
    brand_html: str = ""
    transition: str = "cube"
    translate_langs: list[str] | None = None  # None keeps the current set
    translate_keys: dict[str, str] | None = None  # None keeps the current keys


@router.put("/_api/settings", status_code=204)
async def put_settings(settings: SettingsIn, request: Request) -> None:
    """Update site-wide settings; invalidates cached pages and ETags."""
    with kanta.transaction("settings", user=request.headers.get("remote-user")):
        data.brand = settings.brand
        data.brand_html = settings.brand_html
        data.theme = settings.theme
        data.custom_css = settings.custom_css
        data.transition = settings.transition
        if settings.translate_langs is not None:
            # Any language may be a target — including the site default
            # (an article in another language can be translated INTO it);
            # a node's own primary is excluded per article, not here.
            data.translate_langs = {
                tag: True
                for lang in settings.translate_langs
                if (tag := i18n.base_tag(lang))
            }
        if settings.translate_keys is not None:
            data.translate_keys = settings.translate_keys
        _invalidate_pages()


@router.delete("/_api/translations", status_code=204)
async def delete_translations(request: Request) -> None:
    """Drop all machine translations (Data.trans) so the dispatcher
    re-translates everything from scratch (a translate:reset action:
    the invalidation hook re-offers every fragment to connected
    translators). User patches are kept; the availability index
    (node.langs) is rebuilt from them — patches alone still make a language
    exist on a page."""
    with kanta.transaction("translate:reset", user=request.headers.get("remote-user")):
        i18n.clear_translations(data)
        _invalidate_pages()
    # Fragments rejected this run (segment validation) stay skipped no
    # longer: a refresh is precisely the "another chance" for them.
    dispatcher.reset_validation_failures()


class ToggleTaskIn(BaseModel):
    """Payload for toggling one task-list checkbox."""

    path: str
    index: int
    markdown: str | None = None


@router.post("/_api/toggle-task")
async def toggle_task_endpoint(body: ToggleTaskIn, request: Request) -> dict[str, str]:
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
    if node is None or node.chunks is None:
        raise HTTPException(404, "no such page")
    new_markdown = toggle_task(node_markdown(data, node) or "", body.index)
    if new_markdown is None:
        raise HTTPException(400, "invalid task index")
    with kanta.transaction("page", user=request.headers.get("remote-user"), extra=path):
        # Re-chunk like any save: only the chunk containing the toggled
        # checkbox gets a new hash, the rest keep theirs.
        node.chunks = store_chunks(data.chunks, new_markdown)
        node.modified = datetime.now(UTC)
        _invalidate_pages()
    return {"markdown": new_markdown}


# WebSocket API for external translation services (not under /_api: it is keyed
# with Data.translate_keys instead of the SSO forward-auth). The dispatcher —
# protocol, connected clients and the job pipeline — lives in translate.py.
@router.websocket("/_translate/{clientkey}")
async def translate_ws(ws: WebSocket, clientkey: str) -> None:
    """Translator service channel (docs/localization.md).

    Deliberately NOT under /_api/: the external forward-auth is skipped;
    the server-generated client key in the path is the access control
    (``Data.translate_keys``: key -> display name; the first is generated
    at bootstrap, all are shown in the admin's /_api/settings).
    """
    await dispatcher.handle_ws(ws, clientkey)


@router.websocket("/_api/ws/editor")
async def editor_ws(ws: WebSocket) -> None:
    """Editor session: open pages, render previews, save — over one socket.

    Stateless protocol (each message carries the path):
      <- {"type": "open", "path", "lang"?}
      -> {"type": "doc", "path", "exists", "title", "markdown", "published",
          "banner", "banner_design", "lang", "primary_lang", "langs",
          "translate_langs"}
      <- {"type": "render", "path", "markdown"}
      -> {"type": "html", "path", "html"}
      <- {"type": "save", "path", "title"?, "markdown"?, "published"?,
          "banner"?, "banner_design"?, "move_from"?, "lang"?, "base"?}
          (absent fields keep their old values; move_from: rename/move a
          page, subtree included)
      -> {"type": "saved", "path"} | {"type": "error", "detail"}

    With "lang" (a translation, not the primary language), open returns the
    effective hybrid Markdown and title for that language plus the language
    metadata the picker's UI needs; save diffs the submitted Markdown
    against "base" (the editor's shadow copy of the hybrid it started from
    — absent: the current hybrid) and stores it as a user Patch, and a
    changed title becomes a fragment in Data.trans — node.chunks and the
    other fields stay untouched (docs/localization.md).
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
                    # The article's primary language: its own setting,
                    # inherited down the tree ("en" final fallback).
                    node_lang = i18n.primary_lang(data.menu, path)
                    lang = i18n.base_tag(str(msg.get("lang") or ""))
                    if lang == node_lang:
                        lang = ""
                    markdown = ""
                    title = node.title if node else ""
                    if node is not None:
                        markdown = node_markdown(data, node) or ""
                        if lang and node.chunks is not None:
                            # Translation view: the effective (hybrid)
                            # Markdown and title for that language —
                            # machine fragments + user patches over the
                            # original (docs/localization.md editor flow).
                            markdown = i18n.hybrid_markdown(data, node, path, lang)
                            title = i18n.title_map(data, lang).get(path) or title
                    await ws.send_json(
                        {
                            "type": "doc",
                            "path": path,
                            "exists": node is not None,
                            "title": title,
                            "markdown": markdown,
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
                            # Language context for the editor's picker: the
                            # language this Markdown represents ("" = primary),
                            # the page's own primary language, the translations
                            # this page already has, and the site-wide
                            # configured target languages.
                            "lang": lang,
                            "primary_lang": node_lang,
                            "langs": sorted(node.langs) if node else [],
                            "translate_langs": sorted(data.translate_langs),
                        }
                    )
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
                        # Pin section anchors to the original language so the
                        # preview of a translation matches the served page
                        # (no-op when the previewed markdown is the original).
                        anchors_from=(
                            (node_markdown(data, node) or "", node.title)
                            if node
                            else None
                        ),
                    )
                    await ws.send_json(
                        {
                            "type": "html",
                            "path": path,
                            "html": rendered.html,
                            # Column-layout flag: the preview toggles the
                            # article's .multicol class and swaps in the
                            # segmented (.colseg/.cols) article html.
                            "multicol": rendered.multicol,
                        }
                    )
                case "save":
                    move_from = (msg.get("move_from") or path).strip("/")
                    lang = i18n.base_tag(str(msg.get("lang") or ""))
                    translated = bool(
                        lang and lang != i18n.primary_lang(data.menu, move_from)
                    )
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
                            await ws.send_json(
                                {
                                    "type": "error",
                                    "detail": "cannot move a page under itself",
                                }
                            )
                            continue
                        tslug = path.rpartition("/")[2]
                        if not tslug and old.children:
                            await ws.send_json(
                                {
                                    "type": "error",
                                    "detail": "the front page cannot have children",
                                }
                            )
                            continue
                        tchain = resolve(data.menu, path)
                        if tchain is not None:
                            await ws.send_json(
                                {
                                    "type": "error",
                                    "detail": "target path exists",
                                }
                            )
                            continue
                    if translated and (
                        move_from != path or old is None or old.chunks is None
                    ):
                        # A translated-view save patches an existing
                        # original; it cannot create or move pages.
                        await ws.send_json({"type": "error", "detail": "no such page"})
                        continue
                    if translated and "markdown" in msg and not msg["markdown"].strip():
                        # Saving never deletes; an emptied translation would
                        # render as a blank page in that language.
                        await ws.send_json(
                            {
                                "type": "error",
                                "detail": "a translation cannot be emptied",
                            }
                        )
                        continue
                    with kanta.transaction(
                        f"page:{lang}" if translated else "page",
                        user=ws.headers.get("remote-user"),
                        extra=path,
                    ):
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
                        if translated:
                            # node.chunks and the original-language fields
                            # stay untouched: the markdown diff (against the
                            # editor's shadow "base" — the hybrid it started
                            # from; absent: the current hybrid) is appended
                            # as a Patch, a changed title becomes a
                            # per-language title override (i18n).
                            changed = False
                            if "markdown" in msg:
                                base = msg.get("base")
                                changed = i18n.add_patch(
                                    data,
                                    node,
                                    path,
                                    lang,
                                    msg["markdown"],
                                    base=base if isinstance(base, str) else None,
                                )
                            if "title" in msg and node.title:
                                changed = (
                                    i18n.set_title_translation(
                                        data, node, lang, msg["title"]
                                    )
                                    or changed
                                )
                            if changed:
                                _invalidate_pages()
                        else:
                            if "markdown" in msg:
                                # Saving never deletes; empty markdown is an
                                # empty page. Deletion is an explicit choice
                                # by the page editor (REST DELETE).
                                node.chunks = store_chunks(data.chunks, msg["markdown"])
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
