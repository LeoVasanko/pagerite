"""Shared core: site constants, the kanta database, and the render cache.

Everything the route modules (files, api, tracking, pages) need that is not
a route itself: environment-derived paths and tunables, the ``Data`` root
with its ``Kanta`` handle (migrations in pagerite.migrations), the analytics
store, the fastapi-vue ``Frontend``, the page render cache
(``_render_html``/``_cached_body``/``_html_response`` plus the
``_render_gen`` ETag generation, bumped by ``_invalidate_pages`` on every
content/settings write), the translator ``dispatcher``, the slug charset
helpers, and the database bootstrap hooks (demo seed, translator defaults).
Importable by every other pagerite module without cycles.
"""

import logging
import os
import re
import secrets
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import blake3
from fastapi import HTTPException, Request
from fastapi.responses import Response
from fastapi_vue import Frontend
from kanta import Kanta
from zstandard import ZstdCompressor

from pagerite import analytics, i18n, seed, translate, views
from pagerite.__main__ import DEVMODE
from pagerite.chunks import store_chunks
from pagerite.data import (
    Data,
    Node,
    append_order,
    find_slot,
    prettify,
)

logger = logging.getLogger(__name__)

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

# Our own data root; kanta edits it in place, reads are plain attribute access.
data = Data()
kanta = Kanta(DB_PATH, data, migrations="pagerite.migrations")

# Vue build served at the site root, no SPA catch-all (assets only). The
# build mirrors the URL space: hashed, immutable files live under
# /_assets/ (assetsDir: '_/assets'), the favicon at /favicon.ico.
BUILD_DIR = Path(__file__).with_name("frontend-build")
frontend = Frontend(BUILD_DIR, spa=False, cached="/_assets/")

# Dynamic HTML is compressed per request at level 9 (static assets are
# already pre-compressed by fastapi-vue's Frontend).
_zstd = ZstdCompressor(9)


def _render_html(
    kind: str,
    path: str,
    base_url: str,
    lang: str = i18n.ORIGINAL_LANGUAGE,
    link_lang: str = "",
) -> str:
    """Render one of the generated pages (see _html_response)."""
    if kind == "page":
        # A selected language without an actual translation renders the
        # original (translation is None; see docs/localization.md).
        original = i18n.primary_lang(data.menu, path)
        translation = (
            i18n.get_translation(data, path, lang) if lang != original else None
        )
        return views.render_page(
            data.menu,
            data,
            path,
            data.brand,
            data.custom_css,
            data.theme,
            data.favicon,
            data.brand_html,
            base_url,
            transition=data.transition,
            lang=lang,
            translation=translation,
            link_lang=link_lang,
        )
    if kind == "category":
        # A category has no Markdown of its own; only the title map
        # localizes (heading, navigation, card text).
        original = i18n.primary_lang(data.menu, path)
        translation = (
            i18n.Translation(titles=i18n.title_map(data, lang))
            if lang != original
            else None
        )
        return views.render_category(
            data.menu,
            data,
            path,
            data.brand,
            data.custom_css,
            data.theme,
            data.favicon,
            data.brand_html,
            transition=data.transition,
            lang=lang,
            translation=translation,
            link_lang=link_lang,
        )
    if kind == "not-found":
        return views.render_not_found(
            data.menu,
            path,
            data.brand,
            data.custom_css,
            data.theme,
            data.favicon,
            data.brand_html,
            transition=data.transition,
        )
    return views.render_analytics(
        data.menu,
        data.brand,
        data.custom_css,
        data.theme,
        data.favicon,
        data.brand_html,
        transition=data.transition,
    )


# Render generation: bumped (and the body cache cleared) by every
# content/settings write, so page ETags and cached copies invalidate when
# navigation-affecting changes happen. In-memory only — not database state.
_render_gen = 0


def _invalidate_pages() -> None:
    """Drop cached page bodies and bump the render generation (ETags);
    any content change also re-runs translation dispatch."""
    global _render_gen
    _render_gen += 1
    _cached_body.cache_clear()
    dispatcher.schedule()


@lru_cache(maxsize=128)
def _cached_body(
    kind: str,
    path: str,
    base_url: str,
    zstd: bool,
    lang: str = i18n.ORIGINAL_LANGUAGE,
    link_lang: str = "",
) -> bytes:
    """Rendered page body; cleared by _invalidate_pages on any
    content/settings change. base_url feeds the social meta URLs, zstd
    selects the stored encoding (both variants are cached rather than
    re-compressed) and lang the selected language (not the raw
    Accept-Language header, which would blow up the cache key space).
    link_lang is the ?lang= override replicated onto the navigation links:
    a query render and a header-selected render of the same language differ
    in their links, so they are cached separately.
    """
    body = _render_html(kind, path, base_url, lang, link_lang).encode()
    return _zstd.compress(body) if zstd else body


def _html_response(
    request: Request,
    kind: str,
    path: str,
    status_code: int = 200,
    headers: dict | None = None,
    etag: bool = False,
    lang: str = i18n.ORIGINAL_LANGUAGE,
    link_lang: str = "",
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
        identity = _render_html(kind, path, base_url, lang, link_lang).encode()
        body = _zstd.compress(identity) if zstd else identity
    else:
        identity = _cached_body(kind, path, base_url, False, lang, link_lang)
        body = (
            _cached_body(kind, path, base_url, True, lang, link_lang)
            if zstd
            else identity
        )
    h = dict(headers or {})
    # Content varies by language (Accept-Language selects a translation)
    # and by encoding; keep caches from mixing either representation.
    h["vary"] = "accept-language" + (", accept-encoding" if zstd else "")
    if etag:
        tag = f'"{blake3.blake3(identity).hexdigest()[:32]}"'
        h["etag"] = tag
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=h)
    if zstd:
        h["content-encoding"] = "zstd"
    return Response(body, status_code, h, media_type="text/html")


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


def _remove_page(menu: dict[str, Node], path: str) -> bool:
    """Delete the node at ``path`` (inside a transaction).

    A node with children becomes a content-less category label; a childless
    node is removed entirely. Returns False if the path does not exist.
    """
    slot = find_slot(menu, path)
    node = slot[0].get(slot[1]) if slot else None
    if node is None:
        return False
    if node.children:
        node.chunks = None
        node.modified = datetime.now(UTC)
    else:
        del slot[0][slot[1]]
    return True


def _store_seed_file(
    markdown: str, banner: str, orig: str, body: bytes
) -> tuple[str, str]:
    """Store a seed file content-addressed and point references at /_f/.

    Images get the same AVIF/WebP/JPEG derivatives as uploads and are
    linked extension-less; other content is stored as-is with its
    extension."""
    from pagerite.files import _ext, store_image  # lazy: files imports state

    ext = _ext(orig)
    name = store_image(body, ext, derive=ext != ".gif")
    markdown = markdown.replace(f"]({orig}", f"](/_f/{name}")
    banner = banner.replace(f'src="/{orig}"', f'src="/_f/{name}"')
    banner = banner.replace(f'src="{orig}"', f'src="/_f/{name}"')
    return markdown, banner


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
        # seeded only to carry a banner design): leave chunks as None so
        # the node renders the placeholder and nav points at its children.
        if markdown:
            node.chunks = store_chunks(data.chunks, markdown)
        node.banner = banner
        node.banner_design = design
        node.order = order


#: Translator key format: 12 lowercase alphanumeric characters — not
#: brute-forceable over a WebSocket handshake, still human-manageable.
_KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


@kanta.bootstrap
def _translator_defaults(data: Data) -> None:
    """Translator defaults on database creation: the first service key and
    the wanted target languages (Spanish and Chinese — English is the
    original language, never a translation target).

    Keys are a dict (key -> display name) with the future reservation that
    multiple keys could be managed (e.g. via a web interface)."""
    key = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(12))
    data.translate_keys[key] = "default"
    data.translate_langs = {"es": True, "zh": True}


# The translator dispatcher — protocol, connected clients and the job
# pipeline live in translate.py; its WebSocket route is in api.py.
dispatcher = translate.Dispatcher(data, kanta, _invalidate_pages)
