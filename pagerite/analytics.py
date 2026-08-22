"""Server-side visit analytics (collection only; see docs/analytics.md).

Events come from navigation pings POSTed to /_a by pagerite.js: the first
ping on page load starts a visit, later pings extend it, and pings with no
known session start a fresh one (missing data, not dropped). The document
GET handler stashes the entry referer (external https origin) and any
utm_* query parameters in in-memory IP tables, consumed when the ping
starts the visit; nothing is counted without a ping (plain bots that only
fetch documents end up in the crawler list).  JS-running crawlers
(Googlebot, GoogleOther, Applebot, ...) do ping, but their UA gives them
away (``_is_bot_ua``) and their pings are ignored, so they land in the
crawler list too.  Idle-time link preloads from pagerite.js carry an
``x-pagerite-preload`` header and are not tracked at all — the ping sent
when the user actually navigates does the counting.
Admin clients ping with ``hide=1``, which records nothing and removes any
visit the session accumulated before logging in.  Scanner telltale 404s
(dotpaths, *.php) classify the source IP as abuse; its hits — including
earlier crawler hits — are moved to the abuse list, which the viewer
groups by IP with full request paths.  Client metadata (IP, UA, language,
country/city, host) is stored once per unique client hash and referenced
from visits, crawler hits and abuse hits.  The session map is in-memory
only.

Data is a msgspec Struct JSON-dumped to its own file (not the kanta db),
rewritten atomically on every recorded event.
"""

import ipaddress
import os
import re
import tempfile
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import blake3
import msgspec
from ua_parser import parse


def _compact_user_agent(ua: str) -> str:
    """Format a User-Agent string into a compact display form.

    Returns the original UA when the parser cannot identify the browser/OS.
    """
    if not ua or not ua.strip() or ua == "-":
        return ""
    r = parse(ua)
    browser = r.user_agent.family if r.user_agent else None
    ver = r.user_agent.major if r.user_agent else ""
    os_name = r.os.family if r.os else None
    dev = r.device.family if r.device else None
    if browser in (None, "Other") and os_name in (None, "Other"):
        return ua
    if browser and browser != "Other":
        browser = browser.split()[0]
    else:
        browser = ""
    os_name = os_name if os_name and os_name != "Other" else ""
    if dev in (None, "Other") or dev == browser:
        dev = ""
    parts = [f"{browser}/{ver}" if browser else "", os_name, dev]
    return " ".join(p for p in parts if p).strip()


class Client(msgspec.Struct, omit_defaults=True):
    """Client metadata shared by visits, crawler hits and abuse hits.

    Identified by a 6-byte blake3 hash of the IPv4 address or IPv6 /64
    network, the full User-Agent string and the extracted language tag.
    Country/city/host are filled in asynchronously after the first event.
    """

    #: Visitor IP address (first X-Forwarded-For hop or direct peer).
    ip: str = ""
    #: Reverse-DNS host name for ``ip`` when resolvable, else "".
    host: str = ""
    #: First Accept-Language tag, lowercased (e.g. "en-us").
    lang: str = ""
    #: Two-letter country code from the DB-IP geoip lookup, or "".
    country: str = ""
    #: City name from the DB-IP geoip lookup, or "".
    city: str = ""
    #: Raw User-Agent header.
    ua: str = ""
    #: Compact display form of ``ua`` (browser/OS/device) when parsable.
    ua_pretty: str = ""


class Visit(msgspec.Struct, omit_defaults=True):
    """One visit: the initial-load data plus everything seen afterwards.

    ``trail`` holds page paths and external exit URLs in first-seen
    order; re-visiting an already seen page does not append. The entry
    page itself is in ``entry``, not in the trail.  Client metadata is
    held in ``Analytics.clients`` keyed by ``client``.
    """

    start: datetime
    entry: str
    #: External https origin of the initial load, "" for direct visits.
    referer: str = ""
    #: 6-byte blake3 hash referencing ``Analytics.clients``.
    client: bytes = b""
    trail: list[str] = []
    #: UTM query parameters from the landing URL, keyed by parameter name.
    utm: dict[str, str] = {}
    #: Active reading time per path (seconds), keyed by path.
    read: dict[str, int] = {}


class CrawlerHit(msgspec.Struct, omit_defaults=True):
    """A document GET that was never followed by an analytics ping.

    Client metadata is held in ``Analytics.clients`` keyed by ``client``.
    """

    start: datetime
    entry: str
    #: 6-byte blake3 hash referencing ``Analytics.clients``.
    client: bytes = b""
    #: External https origin of the initial load, "" for direct/none.
    referer: str = ""
    #: Raw query string of the landing URL (UTM tags can be parsed from it).
    query: str = ""


class AbuseHit(msgspec.Struct, omit_defaults=True):
    """A request from an IP classified as a scanner/abuser.

    Unlike crawler hits the full request path (query string included) is
    kept: the interesting part is exactly which paths were probed.
    ``flag`` marks the path that triggered classification; ``is_404``
    distinguishes 404 responses from document GETs made by the abuser.
    Client metadata is held in ``Analytics.clients`` keyed by ``client``.
    """

    start: datetime
    #: Full request path including the query string (e.g. "/.env?x=1").
    path: str
    #: 6-byte blake3 hash referencing ``Analytics.clients``.
    client: bytes = b""
    #: True when this path triggered abuse classification (telltale path
    #: or the 404 that crossed the threshold).
    flag: bool = False
    #: True for 404 responses; false for document GETs from the abuser.
    is_404: bool = False


class Analytics(msgspec.Struct, omit_defaults=True):
    """Root of the analytics JSON file. Append-only by design: old data is
    dropped by deleting list entries / bucket keys."""

    visits: list[Visit] = []
    #: Document GETs that never produced a ping, treated as crawler/bot hits.
    crawlers: list[CrawlerHit] = []
    #: Requests from abusive IPs (see AbuseHit), grouped by IP in the viewer.
    abuse: list[AbuseHit] = []
    #: Client metadata keyed by 6-byte blake3 hash.
    clients: dict[bytes, Client] = {}
    #: IPs classified as scanners/abusers (keys; values always True).
    abuse_ips: dict[str, bool] = {}
    #: Page transitions per 5-minute bucket (sparse):
    #: from -> to -> bucket ISO -> count. ``from`` is the referer origin or
    #: "(direct)" for initial loads, a page path for pings.
    transitions: dict[str, dict[str, dict[str, int]]] = {}
    #: Page views per 5-minute bucket: path -> bucket ISO -> count (sparse).
    views: dict[str, dict[str, int]] = {}
    #: New visits per 5-minute bucket: bucket ISO -> count (sparse).
    site_visits: dict[str, int] = {}


def _bucket(now: datetime) -> str:
    """Start of the 5-minute interval containing ``now``, as ISO string."""
    return now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0).isoformat()


def _origin(url: str) -> str | None:
    """The origin part of an https URL (scheme://host[:port]), else None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return f"https://{parsed.netloc}"


def _external_target(url: str) -> str | None:
    """A valid https URL (origin or full page), else None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return url


_SEGMENT = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _internal_path(to: str) -> str | None:
    """A valid internal page path ("/" or slug segments), else None."""
    path = to.split("?")[0].split("#")[0].strip("/")
    if not path:
        return "/"
    if all(_SEGMENT.fullmatch(seg) for seg in path.split("/")):
        return f"/{path}"
    return None


def _parse_accept_language(value: str) -> tuple[str, str]:
    """First Accept-Language tag and the region/country subtag if present.

    ``en-US, fr;q=0.9`` -> ("en-us", "US").  Wildcards and missing regions
    produce an empty country.  The region is intentionally approximate:
    it reflects the browser's language preference, not geo-location.
    """
    if not value:
        return "", ""
    tag = value.split(",")[0].split(";")[0].strip()
    if not tag or tag == "*":
        return "", ""
    lang = tag.lower()
    country = ""
    # Region subtags follow the initial language tag (en-US, zh-Hans-CN).
    # A bare two-letter tag such as "fr" is a language code, not a region.
    for part in reversed(tag.split("-")[1:]):
        if len(part) == 2 and part.isalpha():
            country = part.upper()
            break
    return lang, country


def _utm_tags(query: str) -> dict[str, str]:
    """UTM parameters from a query string, keeping only the first value."""
    if not query:
        return {}
    parsed = parse_qs(query, keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items() if k.startswith("utm_")}


_CRAWLER_TIMEOUT = timedelta(seconds=10)

#: UAs of JS-running crawlers, which would register as visitors on their
#: ping.  Anything calling itself a "bot" matches; known crawlers without
#: that token (GoogleOther) are listed as extra alternates.  No source
#: verification: a spoofed bot UA just lands in the crawler list, and
#: scanners that probe telltale paths are caught by the abuse rules anyway.
_BOT_UA = re.compile(r"bot|googleother", re.IGNORECASE)


def _is_bot_ua(ua: str) -> bool:
    """True when the UA claims a crawler identity (Googlebot, Applebot, ...)."""
    return bool(_BOT_UA.search(ua))

#: Plain-404 count per IP that classifies it as abuse even without a
#: telltale path hit.
_ABUSE_404_THRESHOLD = 10

#: Paths that instantly classify an IP as abuse when they 404: any segment
#: starting with a dot ("/.env", "/.git/config") or ending in ".php".
_ABUSE_PATH = re.compile(r"(^|/)\.|\.php$", re.IGNORECASE)


def _is_abuse_path(path: str) -> bool:
    """Telltale scanner path: dot segment or *.php."""
    return bool(_ABUSE_PATH.search(path.split("?")[0]))


def _network_ip(ip: str) -> str:
    """IPv4 address unchanged, IPv6 collapsed to its /64 network address.

    We hash the network rather than the full address so that clients in the
    same /64 (a typical end-user allocation) are treated as one visitor.
    """
    if not ip:
        return ip
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network(f"{ip}/64", strict=False).network_address)
    return ip


def _client_hash(ip: str, ua: str, lang: str) -> bytes:
    """6-byte blake3 digest identifying a visitor/client tuple.

    The key is the prettified IP (IPv6 /64), the raw UA string and the
    extracted language tag, separated by null bytes.
    """
    return blake3.blake3(
        f"{_network_ip(ip)}\0{ua}\0{lang}".encode()
    ).digest()[:6]


class Store:
    """In-memory analytics data plus the client-hash -> visit session map."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = Analytics()
        if path.exists():
            try:
                self.data = msgspec.json.decode(path.read_bytes(), type=Analytics)
            except msgspec.DecodeError, OSError:
                pass  # legacy schema / corrupt or unreadable file: start fresh
        #: client hash -> index of the current visit in data.visits
        self.sessions: dict[bytes, int] = {}
        #: ip -> external https origin of the latest document GET carrying
        #: one, stashed for the visit the client's initial ping starts.
        #: Internal or absent referers never touch the table.
        self.pending_referers: dict[str, str] = {}
        #: ip -> utm_* query parameters from the latest document GET that
        #: carried any, stashed for the visit the client's initial ping starts.
        #: Only non-empty sets are stored, so a later parameter-less page
        #: does not overwrite an earlier tagged landing URL.
        self.pending_utms: dict[str, dict[str, str]] = {}
        #: Document GETs that have not yet been matched by a ping.  Kept
        #: in RAM only; expired entries are written to ``data.crawlers``.
        self.pending_crawlers: list[CrawlerHit] = []
        #: ip -> number of plain (non-telltale) 404s seen, in RAM only;
        #: reaching ``_ABUSE_404_THRESHOLD`` classifies the IP as abuse.
        self.not_found_counts: dict[str, int] = {}
        #: Callables to notify when persisted data changes.  Registered by the
        #: analytics WebSocket broadcaster.
        self._on_change: list[Callable[[], None]] = []

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called after every persisted change."""
        if callback not in self._on_change:
            self._on_change.append(callback)

    def unsubscribe(self, callback: Callable[[], None]) -> None:
        """Remove a previously registered change callback."""
        with suppress(ValueError):
            self._on_change.remove(callback)

    def _notify(self) -> None:
        for callback in self._on_change:
            callback()

    def _save(self) -> None:
        """Rewrite the JSON file atomically (temp file + rename)."""
        try:
            fd, tmp = tempfile.mkstemp(
                dir=self.path.parent, prefix=self.path.name, suffix=".tmp"
            )
            with os.fdopen(fd, "wb") as f:
                f.write(msgspec.json.encode(self.data))
            os.replace(tmp, self.path)
        except OSError:
            pass  # analytics must never break page serving
        else:
            self._notify()

    def _flush_crawlers(self, now: datetime | None = None) -> list[bytes]:
        """Move expired pending crawler hits into persistent ``data.crawlers``.

        Returns the client hashes of the newly flushed hits so callers can
        schedule async enrichment.
        """
        if not self.pending_crawlers:
            return []
        now = now or datetime.now(UTC)
        cutoff = now - _CRAWLER_TIMEOUT
        expired: list[CrawlerHit] = []
        remaining: list[CrawlerHit] = []
        for hit in self.pending_crawlers:
            (expired if hit.start <= cutoff else remaining).append(hit)
        if not expired:
            return []
        self.pending_crawlers = remaining
        self.data.crawlers.extend(expired)
        self._save()
        return [hit.client for hit in expired]

    def _count(self, table: dict[str, int], key: str) -> None:
        table[key] = table.get(key, 0) + 1

    def _count_transition(self, fr: str, to: str, now: datetime) -> None:
        """Count one transition in its 5-minute bucket (sparse matrix)."""
        buckets = self.data.transitions.setdefault(fr, {}).setdefault(to, {})
        self._count(buckets, _bucket(now))

    def _uncount(self, table: dict[str, int], key: str) -> None:
        """Reverse one ``_count``: decrement and drop empty keys."""
        if key in table:
            table[key] -= 1
            if table[key] <= 0:
                del table[key]

    def _remove_visit(self, index: int) -> None:
        """Delete a visit and reverse the counts its creation recorded.

        Used when a known visitor turns out to be an admin (hide=1 ping):
        the session is scrubbed from the stats.  Views/transitions logged
        by later pings inside the visit lack per-event timestamps and are
        left as-is.
        """
        visit = self.data.visits[index]
        bucket = _bucket(visit.start)
        self._uncount(self.data.site_visits, bucket)
        views = self.data.views.get(visit.entry)
        if views is not None:
            self._uncount(views, bucket)
            if not views:
                del self.data.views[visit.entry]
        fr_map = self.data.transitions.get(visit.referer or "(direct)")
        if fr_map is not None:
            buckets = fr_map.get(visit.entry)
            if buckets is not None:
                self._uncount(buckets, bucket)
                if not buckets:
                    del fr_map[visit.entry]
            if not fr_map:
                del self.data.transitions[visit.referer or "(direct)"]
        del self.data.visits[index]
        # Sessions store list indices; shift the ones past the removed visit.
        for key, i in list(self.sessions.items()):
            if i > index:
                self.sessions[key] = i - 1

    def _client_ip(self, client_hash: bytes) -> str:
        """Return the IP stored for ``client_hash``, or "" if missing."""
        client = self.data.clients.get(client_hash)
        return client.ip if client else ""

    def _ensure_client(
        self,
        ip: str,
        ua: str,
        lang: str,
        *,
        country: str = "",
    ) -> bytes:
        """Get or create a ``Client`` record; return its 6-byte hash."""
        h = _client_hash(ip, ua, lang)
        if h not in self.data.clients:
            self.data.clients[h] = Client(
                ip=ip,
                ua=ua,
                ua_pretty=_compact_user_agent(ua),
                lang=lang,
                country=country,
            )
            self._save()
        return h

    def enrich_client(
        self,
        client_hash: bytes,
        *,
        host: str = "",
        country: str = "",
        city: str = "",
    ) -> None:
        """Fill in host/geoip fields on a client record after async lookups."""
        client = self.data.clients.get(client_hash)
        if client is None:
            return
        changed = False
        if host and not client.host:
            client.host = host
            changed = True
        if country:
            client.country = country
            changed = True
        if city:
            client.city = city
            changed = True
        if changed:
            self._save()

    def _abuse_hit(
        self,
        client_hash: bytes,
        path: str,
        start: datetime | None = None,
        *,
        flag: bool = False,
        is_404: bool = False,
    ) -> None:
        """Append one abuse hit referencing a client by hash."""
        self.data.abuse.append(
            AbuseHit(
                start=start or datetime.now(UTC),
                path=path,
                client=client_hash,
                flag=flag,
                is_404=is_404,
            )
        )

    def classify_abuse(
        self,
        ip: str,
        client_hash: bytes,
        path: str,
        *,
        flag: bool = False,
        is_404: bool = False,
    ) -> None:
        """Classify an IP as a scanner/abuser and record the triggering hit.

        All earlier crawler hits from the same IP (persisted and pending)
        are moved to the abuse list — a random-UA scanner must not pollute
        the crawler stats of the legitimate bots it impersonates.
        """
        if ip not in self.data.abuse_ips:
            self.data.abuse_ips[ip] = True
            moved = [h for h in self.data.crawlers if self._client_ip(h.client) == ip]
            if moved:
                self.data.crawlers = [h for h in self.data.crawlers if self._client_ip(h.client) != ip]
                for h in moved:
                    self._abuse_hit(
                        h.client,
                        h.entry + (f"?{h.query}" if h.query else ""),
                        start=h.start,
                    )
            pending = [h for h in self.pending_crawlers if self._client_ip(h.client) == ip]
            if pending:
                self.pending_crawlers = [h for h in self.pending_crawlers if self._client_ip(h.client) != ip]
                for h in pending:
                    self._abuse_hit(
                        h.client,
                        h.entry + (f"?{h.query}" if h.query else ""),
                        start=h.start,
                    )
        self._abuse_hit(client_hash, path, flag=flag, is_404=is_404)
        self._save()

    def track_404(
        self,
        ip: str,
        ua: str,
        path: str,
        accept_language: str = "",
    ) -> bytes:
        """Record a 404 response for ``path`` (full path, query included).

        A telltale path (dot segment or *.php) classifies the IP as abuse
        immediately; enough plain 404s from one IP do too.  Hits from
        already-classified IPs go straight to the abuse list.

        Returns the client hash so callers can schedule async enrichment.
        """
        lang, country = _parse_accept_language(accept_language)
        client_hash = self._ensure_client(ip, ua, lang, country=country)
        if ip in self.data.abuse_ips:
            self._abuse_hit(client_hash, path, flag=_is_abuse_path(path), is_404=True)
            self._save()
            return client_hash
        if _is_abuse_path(path):
            self.classify_abuse(ip, client_hash, path, flag=True, is_404=True)
            return client_hash
        self.not_found_counts[ip] = self.not_found_counts.get(ip, 0) + 1
        if self.not_found_counts[ip] >= _ABUSE_404_THRESHOLD:
            self.classify_abuse(ip, client_hash, path, flag=True, is_404=True)
            return client_hash
        return client_hash

    def _new_visit(
        self,
        entry: str,
        referer: str,
        client_hash: bytes,
        utm: dict[str, str] | None = None,
    ) -> Visit:
        now = datetime.now(UTC)
        visit = Visit(
            start=now,
            entry=entry,
            referer=referer,
            client=client_hash,
            utm=utm or {},
        )
        self.data.visits.append(visit)
        self.sessions[client_hash] = len(self.data.visits) - 1
        self._count(self.data.site_visits, _bucket(now))
        self._count(self.data.views.setdefault(entry, {}), _bucket(now))
        self._count_transition(referer or "(direct)", entry, now)
        return visit

    def track_entry(
        self,
        referer: str,
        own_origin: str,
        ip: str,
        ua: str,
        full_path: str,
        accept_language: str = "",
    ) -> list[bytes]:
        """Stash the entry referer/UTM tags and queue a pending crawler hit.

        Nothing is counted here — the client's initial /_a ping starts the
        visit (only non-admin clients ping). Only a cross-origin https
        referer updates the table; an internal or absent referer leaves any
        stashed origin untouched.  UTM parameters are kept only when the
        landing URL actually carries them, so a subsequent parameter-less page
        does not erase an earlier tagged landing.

        Every document GET is also queued as a pending crawler hit.  If a ping
        from the same client arrives within ``_CRAWLER_TIMEOUT``, the hit is
        discarded; otherwise it is flushed to ``data.crawlers``.  The
        Accept-Language header is stored on the client record immediately;
        host/geoip are filled in later by async enrichment.

        GETs from IPs already classified as abuse are recorded as abuse hits
        with the full request path (query string included).

        Returns the client hashes of any hits flushed to persistent storage,
        so callers can schedule async enrichment.
        """
        entry = full_path.split("?")[0]
        query = full_path.split("?", 1)[1] if "?" in full_path else ""
        lang, country = _parse_accept_language(accept_language)
        client_hash = self._ensure_client(ip, ua, lang, country=country)
        if ip in self.data.abuse_ips:
            flushed = self._flush_crawlers()
            self._abuse_hit(client_hash, full_path, is_404=False, flag=False)
            self._save()
            return flushed
        now = datetime.now(UTC)
        flushed = self._flush_crawlers(now)
        if referer:
            origin = _origin(referer)
            if origin is not None and origin != own_origin:
                self.pending_referers[ip] = origin
        utms = _utm_tags(query)
        if utms:
            self.pending_utms[ip] = utms
        self.pending_crawlers.append(
            CrawlerHit(
                start=now,
                entry=entry,
                client=client_hash,
                referer=self.pending_referers.get(ip, ""),
                query=query,
            )
        )
        return flushed

    def _add_read(self, client_hash: bytes, path: str, seconds: int) -> None:
        """Add ``seconds`` of reading time for ``path`` to the current visit."""
        if seconds <= 0:
            return
        index = self.sessions.get(client_hash)
        if index is None or index >= len(self.data.visits):
            return
        visit = self.data.visits[index]
        visit.read[path] = visit.read.get(path, 0) + seconds

    def ping(
        self,
        from_: str,
        to: str | None,
        ip: str,
        ua: str,
        accept_language: str = "",
        hide: bool = False,
        read: int = 0,
    ) -> tuple[int | None, list[bytes]]:
        """Record a client navigation ping ({from, to, read} from pagerite.js).

        ``to`` is an internal path ("/...") or an https URL for exit links; a
        missing/empty ``to`` means the page is being closed and only the
        ``read`` time should be recorded. The transition is always counted when
        ``to`` is present; the trail only grows on first sight of a page within
        the visit. ``read`` is the active time (seconds) spent on ``from_``.

        A ping with no known session starts a fresh visit, consuming the
        referer and UTM tags stashed by the document GET if there are any.

        ``hide`` is set by admin clients: the ping cancels pending crawler
        hits as usual, and any existing visit for this client session is
        removed from the stats (the admin browsed anonymously before logging
        in).  Nothing new is recorded.

        Pings from IPs classified as abuse, and pings whose User-Agent
        claims a JS-running crawler identity (``_is_bot_ua``), are ignored
        entirely — the crawler's pending hits stay queued and flush to
        ``data.crawlers`` normally.

        Returns the index of the new visit when one is created (or None) and
        the client hashes of any crawler hits flushed by this call, so callers
        can schedule async enrichment (host, geoip country/city).
        """
        flushed = self._flush_crawlers()
        lang, country = _parse_accept_language(accept_language)
        client_hash = _client_hash(ip, ua, lang)
        if hide:
            # Admin ping: cancel pending crawler hits and scrub the session.
            self.pending_crawlers = [
                hit for hit in self.pending_crawlers if hit.client == client_hash
            ]
            index = self.sessions.pop(client_hash, None)
            if index is not None and index < len(self.data.visits):
                self._remove_visit(index)
                self._save()
            return None, flushed
        if ip in self.data.abuse_ips:
            return None, flushed
        if _is_bot_ua(ua):
            # A JS-running crawler (Googlebot, GoogleOther, Applebot execute
            # JS and ping): never a visit.  Its pending crawler hits are
            # kept and flush to ``data.crawlers`` normally.
            return None, flushed
        # A real visitor ping cancels any pending crawler hits from this client.
        self.pending_crawlers = [
            hit for hit in self.pending_crawlers if hit.client != client_hash
        ]
        fr_path = _internal_path(from_) if from_ else ""
        if fr_path and read > 0:
            self._add_read(client_hash, fr_path, read)
        if not to:
            if read > 0:
                self._save()
            return None, flushed
        if to.startswith("/") and not to.startswith("//"):
            target = _internal_path(to) or ""
        else:
            target = _external_target(to) or ""
        if not target:
            return None, flushed
        index = self.sessions.get(client_hash)
        fr = fr_path or "(direct)"
        if index is None or index >= len(self.data.visits):
            # No known session: the initial ping of a fresh page load (or
            # missing data after a server restart) — start a visit.
            index = len(self.data.visits)
            self._ensure_client(ip, ua, lang, country=country)
            self._new_visit(
                target,
                self.pending_referers.pop(ip, ""),
                client_hash,
                utm=self.pending_utms.pop(ip, {}),
            )
        else:
            visit = self.data.visits[index]
            now = datetime.now(UTC)
            if target.startswith("/"):
                self._count(self.data.views.setdefault(target, {}), _bucket(now))
            self._count_transition(fr, target, now)
            # First-seen only: repeat pages and repeated exits don't append.
            if visit.entry != target and target not in visit.trail:
                visit.trail.append(target)
        self._save()
        visit_index = index if index is not None and index < len(self.data.visits) else None
        return visit_index, flushed
