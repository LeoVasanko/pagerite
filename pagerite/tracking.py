"""Visit analytics: collection sockets, geoip enrichment, favicon fetch.

The visitor-activity WebSocket (``/_ws``, public) and the admin analytics
stream (``/_api/ws/analytics``) plus the ``/_a`` viewer page. Client IPs are
enriched in background tasks with reverse DNS (cached PTR lookups) and the
DB-IP city MMDB (``GeoIP``, decompressed and opened once at startup);
external referrers get their favicon fetched and stored content-hashed.
Snapshot broadcasts to connected admin sockets are debounced.
"""

import asyncio
import gzip
import ipaddress
import logging
import os
import re
import shutil
import socket
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import httpx
import msgspec
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from pagerite import analytics
from pagerite.files import _hash_name, file_store
from pagerite.state import SITE_URL, _html_response, analytics_store

logger = logging.getLogger(__name__)

router = APIRouter()

# Live WebSocket clients for the analytics stream.
_analytics_ws_clients: set[WebSocket] = set()
_analytics_broadcast_task: asyncio.Task | None = None


# Repository root from this file's location (pagerite/tracking.py -> ..).
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


def _client_ip(request: Request | WebSocket) -> str:
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
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_link_local
    ):
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
        if (
            not (200 <= r.status_code < 300)
            or not body
            or len(body) > _FAVICON_MAX_BYTES
        ):
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
    except httpx.HTTPError, OSError:
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


def _track_entry(path: str, request: Request, *, status: int = 200) -> list[bytes]:
    """Stash the referer/UTM tags and queue a pending crawler hit for the GET.

    Nothing is counted on the GET itself — the client's first /_ws message
    starts the visit, so bots never register as visits (JS-running crawlers
    connect too, but the WebSocket handler ignores known bot UAs).  (Admin
    clients report too, but with hide, which flags their visit hidden: it is
    recorded but excluded from all statistics and from the crawler list.)

    The devserver's health probe (``GET /?from=devserver.py`` from
    ``127.0.0.1``) is ignored: it is not real traffic and would otherwise be
    logged as a crawler hit.  The root-path and localhost checks prevent
    remote visitors from hiding traffic with the same query string.

    Returns the client hashes of any pending crawler hits flushed to persistent
    storage, so callers can schedule async geoip and reverse-DNS enrichment.
    """
    if request.headers.get("x-pagerite-preload"):
        # Idle-time page-cache warm-up by pagerite.js, not a page view: the
        # activity message sent when the user actually navigates does the
        # counting.
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


@router.get("/_a", response_model=None)
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


@router.websocket("/_ws")
async def activity_ws(ws: WebSocket) -> None:
    """Collect visitor activity: navigations and reading-time updates.

    Public, like the pages themselves (only /_api is gated); one connection
    follows a browsing session.  Messages are ``analytics.Ping`` structs as
    JSON text frames; ``to`` set is a navigation, ``read`` alone a
    reading-time update.  The reverse-DNS and DB-IP geoip lookups happen in
    background tasks so message handling is never delayed by slow DNS or
    the first MMDB decompress.
    """
    await ws.accept()
    ip = _client_ip(ws)
    ua = ws.headers.get("user-agent", "")
    accept_language = ws.headers.get("accept-language", "")
    try:
        while True:
            text = await ws.receive_text()
            try:
                msg = msgspec.json.decode(text.encode(), type=analytics.Ping)
            except msgspec.DecodeError:
                continue
            visit_index, flushed_clients = analytics_store.ping(
                msg.fr,
                msg.to or None,
                ip,
                ua,
                accept_language,
                hide=msg.hide,
                read=msg.read,
            )
            if visit_index is not None:
                visit = analytics_store.data.visits[visit_index]
                asyncio.create_task(_enrich_client(visit.client))
            _schedule_client_enrichment(flushed_clients)
            _schedule_favicon_fetch()
    except WebSocketDisconnect:
        pass


@router.websocket("/_api/ws/analytics")
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
