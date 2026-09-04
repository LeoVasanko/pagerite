"""Visit analytics: collection sockets, geoip enrichment, favicon fetch.

The visitor-activity WebSocket (``/_ws``, public) and the admin analytics
stream (``/_api/ws/analytics``) plus the ``/_a`` viewer page. Client IPs are
enriched in background tasks with reverse DNS (cached PTR lookups) and the
DB-IP city MMDB (``GeoIP``, decompressed into RAM and opened once at
startup);
external referrers get their favicon fetched and stored content-hashed.
Snapshot broadcasts to connected admin sockets are debounced.
"""

import asyncio
import gzip
import io
import ipaddress
import logging
import os
import re
import socket
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import httpx
import msgspec
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from pagerite import analytics
from pagerite.data import resolve
from pagerite.files import _hash_name, file_store
from pagerite.state import SITE_URL, _html_response, analytics_store, data

logger = logging.getLogger(__name__)

# httpx logs every request at INFO (e.g. the favicon fetches below); our own
# one-line summary in _schedule_favicon_fetch replaces that noise.
logging.getLogger("httpx").setLevel(logging.WARNING)

router = APIRouter()

# Live WebSocket clients for the analytics stream.
_analytics_ws_clients: set[WebSocket] = set()
_analytics_broadcast_task: asyncio.Task | None = None


# DB-IP databases persist in the working directory (one download serves all
# sites run from it). Not the package directory: reinstalls/upgrades wipe it.
_DBIP_DIR = Path.cwd()

DBIP_URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"


def _download_dbip() -> None:
    """Download the latest dbip-city-lite MMDB if ours is missing or older."""
    today = date.today()
    months = [f"{today:%Y-%m}"]
    # The current month's file may not be published yet; fall back to last month.
    prev = (today.replace(day=1) - date.resolution).replace(day=1)
    months.append(f"{prev:%Y-%m}")

    existing = sorted(
        p.stem.removeprefix("dbip-city-lite-").removesuffix(".mmdb")
        for p in _DBIP_DIR.glob("dbip-city-lite-*.mmdb*")
    )
    if existing and existing[-1] >= months[0]:
        logger.info("DB-IP database is current (%s), skipping download", existing[-1])
        return

    for month in months:
        url = DBIP_URL.format(month=month)
        target = _DBIP_DIR / f"dbip-city-lite-{month}.mmdb.gz"
        tmp = target.with_suffix(".mmdb.gz.tmp")
        logger.info("Downloading %s", url)
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as e:
            logger.warning("DB-IP download failed: %s", e)
            tmp.unlink(missing_ok=True)
            continue
        # Verify it is actually gzip data before installing it.
        try:
            with gzip.open(tmp, "rb") as f:
                f.read(1)
        except OSError:
            logger.warning("DB-IP download for %s was not valid gzip", month)
            tmp.unlink(missing_ok=True)
            continue
        os.replace(tmp, target)
        # Drop older databases so the app never picks up a stale one.
        for old in _DBIP_DIR.glob("dbip-city-lite-*.mmdb*"):
            if old.name != target.name:
                old.unlink()
        logger.info("DB-IP database updated to %s", target.name)
        return
    logger.warning("Could not download a DB-IP database")


def _geoip_db_path() -> Path | None:
    """Find a DB-IP MMDB in the working directory: the ``.mmdb.gz`` download
    is canonical (decompressed into RAM at open); a plain ``.mmdb`` left over
    from older versions is still usable, and removed once the matching ``.gz``
    is present so it does not linger on disk.  Returns None if none is present.
    """
    gz = sorted(_DBIP_DIR.glob("dbip-*.mmdb.gz"))
    if gz:
        for stale in _DBIP_DIR.glob("dbip-*.mmdb"):
            stale.unlink()
        return gz[0]
    mmdb = sorted(_DBIP_DIR.glob("dbip-*.mmdb"))
    if mmdb:
        return mmdb[0]
    return None


class GeoIP:
    """Lazy DB-IP MMDB reader.  Call ``_load()`` once at startup before
    concurrent requests arrive; ``country()`` is read-only and safe to call
    from ``asyncio.to_thread`` workers afterwards.
    """

    def __init__(self) -> None:
        self._reader: object | None = None

    def _load(self) -> None:
        if self._reader is not None:
            return
        source = _geoip_db_path()
        if source is None:
            return
        try:
            import maxminddb

            if source.suffix == ".gz":
                # Only the .gz is kept on disk; the database is decompressed
                # into RAM (MODE_FD makes the pure-Python Reader .read() the
                # buffer — never mmap — and bypasses the C extension).
                buf = io.BytesIO(gzip.decompress(source.read_bytes()))
                self._reader = maxminddb.open_database(buf, maxminddb.MODE_FD)
            else:
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
    origins = [
        origin
        for origin in analytics_store.favicon_origins_needed()
        if origin not in _favicon_in_flight
    ]
    if not origins:
        return
    logger.info(
        "Fetching favicons: %s",
        ", ".join(o.removeprefix("https://") for o in origins),
    )
    for origin in origins:
        _favicon_in_flight.add(origin)
        asyncio.create_task(_fetch_favicon(origin))


async def _broadcast_analytics() -> None:
    """Send the current analytics snapshot to every connected WS client."""
    if not _analytics_ws_clients:
        return
    payload = analytics_store.display_json(_in_menu)
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


def _in_menu(path: str) -> bool:
    """True when ``path`` ("/a/b" or "/") resolves to a real menu node.

    Category placeholders return 404 but are real nodes: their GETs must not
    count as misses in the display-time abuse classification.
    """
    return resolve(data.menu, path.strip("/")) is not None


def _record_get(request: Request, *, status: int = 200) -> None:
    """Record the document GET as one raw access-log line in analytics.

    Nothing is classified here — the true HTTP status, the full request path
    (query included), an external referer origin and the preload flag are
    stored, and visitor/crawler/abuse classification happens at display time
    (see analytics.Store.display).  Idle-time preloads from pagerite.js
    (``x-pagerite-preload`` header) are recorded with ``pre=True``: never
    counted, but a navigation later served from the in-memory page cache is
    attributed this GET's status.

    The devserver's health probe (``GET /?from=devserver.py`` from
    ``127.0.0.1``) is ignored: it is not real traffic.  The root-path and
    localhost checks prevent remote visitors from forging the same query.
    """
    if (
        request.url.path == "/"
        and str(request.url.query) == "from=devserver.py"
        and _client_ip(request) == "127.0.0.1"
    ):
        return
    own_origin = SITE_URL or f"https://{urlparse(str(request.base_url)).netloc}"
    referer = request.headers.get("referer", "")
    if analytics._origin(referer) in (None, own_origin):
        referer = ""
    client_hash = analytics_store.record_get(
        _client_ip(request),
        request.headers.get("user-agent", ""),
        f"{request.url.path}{_query_suffix(request)}",
        status=status,
        referer=referer,
        accept_language=request.headers.get("accept-language", ""),
        pre=bool(request.headers.get("x-pagerite-preload")),
    )
    if client_hash is not None:
        _schedule_client_enrichment([client_hash])


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
    reading-time update.  Everything is recorded raw — known bot UAs and
    abusive IPs are filtered at display time, not here.  The reverse-DNS and
    DB-IP geoip lookups happen in background tasks so message handling is
    never delayed by slow DNS or the first MMDB decompress.
    """
    ip = _client_ip(ws)
    ua = ws.headers.get("user-agent", "")
    accept_language = ws.headers.get("accept-language", "")
    # Identify the visitor on the access-log open/close lines (the IP is
    # already printed there): compact UA plus the browser's language tag.
    lang, _country = analytics._parse_accept_language(accept_language)
    ws.scope.setdefault("state", {})["log_extra"] = " ".join(
        part for part in (analytics._compact_user_agent(ua), lang) if part
    )
    await ws.accept()
    try:
        while True:
            text = await ws.receive_text()
            try:
                msg = msgspec.json.decode(text.encode(), type=analytics.Ping)
            except msgspec.DecodeError:
                continue
            new_client = analytics_store.record_msg(
                msg.fr,
                msg.to or None,
                ip,
                ua,
                accept_language,
                hide=msg.hide,
                read=msg.read,
            )
            if new_client is not None:
                _schedule_client_enrichment([new_client])
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
    await ws.send_text(analytics_store.display_json(_in_menu))
    _analytics_ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except Exception:
        pass
    finally:
        _analytics_ws_clients.discard(ws)
