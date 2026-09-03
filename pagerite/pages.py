"""Public content pages: front page, sitemap, robots, and the catch-all.

``GET /{path:path}`` resolves a slug path against the menu tree and renders
the page (or a category placeholder, or 404); it must be registered AFTER
the fastapi-vue asset routes so built frontend files win over content slugs
(see app.py). Requests are recorded in analytics (crawler hits and 404s
here, visits via the /_ws socket in tracking.py).
"""

import asyncio
import logging
from datetime import UTC, datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from pagerite import i18n, state
from pagerite.data import Node, resolve, sorted_nodes
from pagerite.state import (
    SITE_URL,
    _html_response,
    _is_reserved,
    analytics_store,
    data,
)
from pagerite.tracking import (
    _client_ip,
    _enrich_client,
    _query_suffix,
    _schedule_client_enrichment,
    _track_entry,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _http_date(dt: datetime) -> str:
    """RFC 7231 date for the Last-Modified header."""
    return format_datetime(dt.astimezone(UTC), usegmt=True)


def _is_trackable_path(path: str) -> bool:
    """Content URLs only: skip auth endpoints and reserved/machinery paths."""
    if not path:
        return True
    if path == "auth" or path.startswith("auth/"):
        return False
    return not _is_reserved(path)


@router.get("/")
async def front_page(request: Request) -> Response:
    """Render the front page (slug path "")."""
    return await show_page(request, "")


@router.get("/sitemap.xml")
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
                if node.published and node.chunks is not None
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
                and node.chunks is not None
                and depth > 0
            ):
                depth -= 1
            if node.published and node.chunks is not None:
                entries.append((path, node.modified, depth))
            if node.children:
                walk(node.children, path, node.chunks is not None)

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
            modified.astimezone(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
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


@router.get("/robots.txt")
async def robots_txt(request: Request) -> Response:
    """Allow content crawling, keep the SSO login (/auth/) and the
    admin-gated API (/_api) out of search results, and point crawlers at
    the sitemap."""
    base = SITE_URL or str(request.base_url).rstrip("/")
    body = f"User-agent: *\nAllow: /\nDisallow: /auth/\nDisallow: /_api\nSitemap: {base}/sitemap.xml\n"
    return Response(
        body,
        media_type="text/plain",
        headers={"cache-control": "no-cache"},
    )


@router.get("/{path:path}", response_model=None)
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
    if node is not None and node.published and node.chunks is not None:
        # Language selection (docs/localization.md): ?lang= wins when a
        # translation exists, else header logic. Analytics keep the raw
        # Accept-Language header regardless of the selection.
        query_lang = request.query_params.get("lang")
        lang = i18n.select_language(
            query_lang,
            accept_language,
            lambda tag: tag in node.langs,
            original=i18n.primary_lang(data.menu, path),
        )
        # A ?lang= override is replicated onto the page's navigation links
        # (link_lang), so clicks and prefetches stay in the chosen language.
        # Query and header-selected renders of the same language differ in
        # their links, so link_lang is part of the ETag and body cache key.
        link_lang = i18n.base_tag(query_lang or "")
        # no-cache forbids serving a stored page without revalidation
        # (browsers would otherwise cache heuristically and serve stale
        # pages, e.g. after a theme change). In-session speed instead comes
        # from pagerite.js's in-memory page cache (preload everything, never
        # fetch on navigation); the ETag just makes those one-time preload
        # fetches and any revalidation cheap.
        etag = f'"{path}@{node.modified.timestamp()}g{state._render_gen}l{lang}q{link_lang}"'
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
            lang=lang,
            link_lang=link_lang,
        )
    if node is not None and node.published and node.chunks is None:
        # Category label without a landing page: placeholder with the pen
        # to create it (404 — no page here, but the node is real).
        # Language selection as on content pages, but over the whole
        # subtree's availability: the category has no chunks of its own —
        # its heading, the navigation and the cards' text localize from
        # the title map and the target articles' translations.
        query_lang = request.query_params.get("lang")
        subtree_langs = i18n.subtree_languages(node)
        lang = i18n.select_language(
            query_lang,
            accept_language,
            lambda tag: tag in subtree_langs,
            original=i18n.primary_lang(data.menu, path),
        )
        link_lang = i18n.base_tag(query_lang or "")
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
            lang=lang,
            link_lang=link_lang,
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
