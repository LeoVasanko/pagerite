"""Server-side visit analytics (collection only; see docs/analytics.md).

Raw recording, display-time classification.  Every document GET is appended
to ``Analytics.gets`` as a raw access-log line (path with query string, true
HTTP status, external referer origin, preload flag) and every pagerite.js
activity message from the /_ws WebSocket is appended to ``Analytics.msgs``
(navigations ``fr`` -> ``to`` and active reading-time updates).  Nothing is
classified when it is recorded: whether a client turns out to be a reader,
a crawler or a scanner is decided by ``Store.display()`` from the raw
events, so the stored data survives any future change to the classification
rules.

At display time:

- An IP with a 404 on a telltale scanner path (empty segment like
  ``//foo``, dot segment, *.php) or ten plain 404s within an hour on paths
  that don't resolve to a menu node is classified as abuse; every document
  GET from that IP is shown in the abuse list, split by status into the 404
  probes and the real articles read.  Its activity messages are ignored.
  Hidden (admin) clients never trigger classification — editing means
  visiting not-found pages.  RFC 8615 well-known URIs (``/.well-known/…``)
  are mostly legitimate browser/service probes and never count as abuse
  evidence.
- A document GET never followed by an activity message (within
  ``_CRAWLER_TIMEOUT``) is a crawler hit.  Messages whose UA claims a
  crawler identity (``_is_bot_ua``) are ignored, so JS-running crawlers
  (Googlebot, GoogleOther, Applebot, ...) land in the crawler list too.
- The remaining messages are grouped into visits per client: a new visit
  starts after ``_SESSION_GAP`` of inactivity.  A visit whose total
  reported reading time is under ``_MIN_VISIT_READ`` seconds is a
  real-browser bot and is reclassified as crawler hits (durations are
  client-provided and trusted — such bots report 0–2 s).
- Admin clients (``hide`` message field, set at collection time on the
  client record) are excluded from every list and aggregate.

Idle-time link preloads from pagerite.js (``x-pagerite-preload`` header)
are recorded raw but flag ``pre``: they never count as views, crawler hits
or abuse — they exist so a navigation served from the in-memory page cache
can still be attributed the HTTP status of its preload GET.

Aggregates (site visits, page views, transitions) are not stored; they are
computed at display time from the derived visits.  Client metadata (IP, UA,
language, country/city, host) is stored once per unique client hash and
referenced from every event.

Data is a msgspec Struct JSON-dumped to its own file (not the kanta db),
rewritten atomically on every recorded event.
"""

import ipaddress
import os
import re
import tempfile
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import blake3
import msgspec
import uarite


def _compact_user_agent(ua: str) -> str:
    """Format a User-Agent string into a compact display form.

    See ``uarite.uaparse``: crawler name (with category) for bots,
    ``Browser/major OS`` or the device for real browsers, the raw UA when
    unrecognized.
    """
    return uarite.uaparse(ua).pretty


class Ping(msgspec.Struct, omit_defaults=True):
    """One client message on the /_ws activity WebSocket (wire format).

    Sent as a JSON text frame (msgspec-encoded, decoded to str for the
    wire).  ``to`` set: a navigation — internal page path or external https
    exit URL.  ``read`` alone (with ``fr``): an active reading-time update
    for the page ``fr``; these arrive frequently while the user is active.
    ``hide`` flags the client as an admin: everything it ever did is
    excluded from the statistics.
    """

    #: Path of the page the activity happened on ("" for the initial load).
    fr: str = ""
    #: Navigation target: internal path or external https exit URL.
    to: str = ""
    #: Active reading time (seconds) spent on ``fr`` since the last report.
    read: int = 0
    #: Admin client: record but hide everything from the statistics.
    hide: bool = False


class Get(msgspec.Struct, omit_defaults=True):
    """One document GET — the raw access-log line.

    Recorded once per served document (200, or 404 for a category
    placeholder or a missing page); never classified at this point.
    Client metadata is held in ``Analytics.clients`` keyed by ``client``.
    """

    t: datetime
    #: Full request path including the query string (e.g. "/.env?x=1").
    path: str
    #: 6-byte blake3 hash referencing ``Analytics.clients``.
    client: bytes = b""
    #: True HTTP status of the response.
    status: int = 200
    #: External https origin of the Referer, "" for direct/internal.
    ref: str = ""
    #: Idle-time cache warm-up by pagerite.js (x-pagerite-preload header):
    #: never counted as a view/crawler/abuse hit; recorded only so a later
    #: cache-served navigation can be attributed this GET's status.
    pre: bool = False


class Msg(msgspec.Struct, omit_defaults=True):
    """One raw activity message from pagerite.js over /_ws.

    ``to`` set: a navigation — validated internal page path or external
    https exit URL.  ``read`` alone (with ``fr``): an active reading-time
    update for the page ``fr`` (seconds since the previous report).
    Client metadata is held in ``Analytics.clients`` keyed by ``client``.
    """

    t: datetime
    #: 6-byte blake3 hash referencing ``Analytics.clients``.
    client: bytes = b""
    #: Path of the page the activity happened on ("" for the initial load).
    fr: str = ""
    #: Navigation target: internal path or external https exit URL.
    to: str = ""
    #: Active reading time (seconds) spent on ``fr`` since the last report.
    read: int = 0


class Client(msgspec.Struct, omit_defaults=True):
    """Client metadata shared by the raw events and every derived row.

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
    #: True for admin clients (hide=1 ping): everything this client ever did
    #: is excluded from all statistics and from the viewer payload.
    hide: bool = False


# --- Display DTOs -------------------------------------------------------
# The structs below are never persisted; Store.display() builds them from
# the raw events.  They define the viewer payload shape consumed by
# frontend/src/analytics/*.


class Nav(msgspec.Struct, omit_defaults=True):
    """One navigation inside a visit: from ``fr`` to ``to``.

    ``to`` is an internal page path or an external https exit URL.  Every
    navigation is logged (repeats included), keyed by its timestamp in
    ``Visit.navs``, so display-time aggregates can count views and
    transitions; ``Visit.trail`` keeps the first-seen order.
    """

    fr: str
    to: str


class TrailItem(msgspec.Struct, omit_defaults=True):
    """One first-seen target in a visit trail: a page or external exit URL.

    ``read`` accumulates active reading time (seconds) across the whole
    visit; ``status`` is the most recent HTTP status seen for the target.
    """

    to: str
    #: Accumulated active reading time in seconds.
    read: int = 0
    #: Most recent HTTP status of the response (200 or 404).
    status: int = 200


class Visit(msgspec.Struct, omit_defaults=True):
    """One visit: a client's activity since ``_SESSION_GAP`` of inactivity.

    ``trail`` holds the entry page and everything seen afterwards, keyed by
    the timestamp of first sight (insertion order = first-seen order);
    re-visiting an already seen target updates its item instead of
    appending.  Client metadata is held in ``Analytics.clients`` keyed by
    ``client``.
    """

    start: datetime
    entry: str
    #: External https origin of the initial load, "" for direct visits.
    referer: str = ""
    #: 6-byte blake3 hash referencing ``Analytics.clients``.
    client: bytes = b""
    #: First-seen targets keyed by their timestamp (entry included).
    trail: dict[datetime, TrailItem] = {}
    #: Every navigation (repeats included) keyed by its timestamp; the
    #: aggregates are computed from this log at display time.
    navs: dict[datetime, Nav] = {}
    #: UTM query parameters from the landing URL, keyed by parameter name.
    utm: dict[str, str] = {}


class CrawlerHit(msgspec.Struct, omit_defaults=True):
    """A document GET that was never followed by an activity message.

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
    #: HTTP status of the served response (200 or 404 for content pages).
    status: int = 200


class AbuseHit(msgspec.Struct, omit_defaults=True):
    """A document GET from an IP classified as a scanner/abuser.

    Unlike crawler hits the full request path (query string included) is
    kept: the interesting part is exactly which paths were probed.
    ``flag`` marks the paths that triggered classification; ``is_404``
    distinguishes 404 responses (probed paths and 404-fallback document
    GETs) from real 200 document GETs — the abuser actually reading
    articles.  Client metadata is held in ``Analytics.clients`` keyed by
    ``client``.
    """

    start: datetime
    #: Full request path including the query string (e.g. "/.env?x=1").
    path: str
    #: 6-byte blake3 hash referencing ``Analytics.clients``.
    client: bytes = b""
    #: True when this path triggered abuse classification (telltale path
    #: or the 404 that crossed the threshold).
    flag: bool = False
    #: True for 404 responses; false for real (200) document GETs.
    is_404: bool = False


class Favicon(msgspec.Struct, omit_defaults=True):
    """Favicon fetch record for one external https origin.

    The icon itself is stored on disk under a content-hashed name (like
    uploads, but outside the kanta db), referenced here by ``file``; an
    empty ``file`` is a known miss, retried after ``_FAVICON_RETRY``.
    """

    #: Content-hashed file name of the stored icon, "" when the fetch failed.
    file: str = ""
    #: When the fetch was last attempted.
    fetched: datetime | None = None


class Analytics(msgspec.Struct, omit_defaults=True):
    """Root of the analytics JSON file: the raw event log, append-only by
    design.  Old data is dropped by deleting list entries."""

    #: Every document GET, in arrival order (see Get).
    gets: list[Get] = []
    #: Every activity message from pagerite.js, in arrival order (see Msg).
    msgs: list[Msg] = []
    #: Favicon fetch records keyed by external https origin.
    favicons: dict[str, Favicon] = {}
    #: Client metadata keyed by 6-byte blake3 hash.
    clients: dict[bytes, Client] = {}


class Display(msgspec.Struct, omit_defaults=True):
    """The viewer payload: visible derived data plus display-time aggregates.

    Hidden clients are excluded everywhere: their events are dropped, and
    the aggregates are computed from the visible visits only.
    The aggregate shapes match what the viewer consumes: sparse 5-minute
    buckets keyed by their floored ISO timestamp.
    """

    visits: list[Visit] = []
    crawlers: list[CrawlerHit] = []
    abuse: list[AbuseHit] = []
    clients: dict[bytes, Client] = {}
    #: origin -> URL path of the stored favicon ("/_f/<file>"),
    #: only for origins whose icon was fetched successfully.
    favicons: dict[str, str] = {}
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

#: Inactivity after which a client's next navigation starts a new visit.
_SESSION_GAP = timedelta(minutes=30)

#: Minimum total reported reading time (seconds, summed over the trail) for
#: a session to count as a visit; shorter sessions are JS-running bots and
#: are shown as crawler hits instead.
_MIN_VISIT_READ = 5

#: How long a failed favicon fetch suppresses retries for the same origin.
_FAVICON_RETRY = timedelta(days=7)

#: UAs of JS-running crawlers, which would register as visitors on their
#: activity messages.  ``uarite`` knows the common crawlers
#: and link-preview fetchers (including disguised ones such as
#: facebookexternalhit and Google-Extended) plus any UA with a
#: bot/spider/crawler/scanner token.  No source verification: a spoofed
#: bot UA just lands in the crawler list, and scanners that probe
#: telltale paths are caught by the abuse rules anyway.


def _is_bot_ua(ua: str) -> bool:
    """True when the UA is not a regular browser.

    Every real browser registers as ``kind == "browser"``; anything else
    (recognized crawler/previewer, generic bot token, or an unclassified
    HTTP client such as httpx) is not a visitor.
    """
    return uarite.uaparse(ua).kind != "browser"


#: Plain-404 count per IP within ``_ABUSE_404_WINDOW`` that classifies it as
#: abuse even without a telltale path hit.  Windowed so a long-time reader
#: slowly accumulating misses (deleted articles over months) never crosses
#: it — scanners spray their probes in bursts.
_ABUSE_404_THRESHOLD = 10

#: Sliding window the plain-404 threshold is counted over.
_ABUSE_404_WINDOW = timedelta(hours=1)

#: Paths that instantly classify an IP as abuse when they 404: an empty
#: segment ("//foo" — no real client requests those), any segment starting
#: with a dot ("/.env", "/.git/config") or ending in ".php".
_ABUSE_PATH = re.compile(r"/{2,}|(^|/)\.|\.php$", re.IGNORECASE)


def _is_abuse_path(path: str) -> bool:
    """Telltale scanner path: empty segment, dot segment or *.php."""
    return bool(_ABUSE_PATH.search(path.split("?")[0]))


def _is_well_known(path: str) -> bool:
    """RFC 8615 well-known URI ("/.well-known/...").

    Mostly legitimate (browsers and services probe them, e.g. Chrome's
    devtools fetch of appspecific/com.chrome.devtools.json): never telltale
    and never counted toward the plain-404 threshold.
    """
    return path.split("?")[0].lower().startswith("/.well-known/")


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
    return blake3.blake3(f"{_network_ip(ip)}\0{ua}\0{lang}".encode()).digest()[:6]


class Store:
    """In-memory analytics data: the raw event log plus its JSON persistence."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = Analytics()
        if path.exists():
            try:
                raw = path.read_bytes()
            except OSError:
                raw = b""
            # The pre-redesign schema (stored visits/crawlers/abuse lists) is
            # not convertible: set it aside and start fresh.
            legacy = b'"visits"' in raw
            if raw and not legacy:
                try:
                    self.data = msgspec.json.decode(raw, type=Analytics)
                except msgspec.DecodeError:
                    legacy = True  # corrupt file: start fresh
            if legacy:
                with suppress(OSError):
                    path.rename(path.with_name(path.name + ".bak-legacy"))
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

    def _hidden(self, client_hash: bytes) -> bool:
        """True when the client record is flagged hidden (admin)."""
        client = self.data.clients.get(client_hash)
        return client is not None and client.hide

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

    def record_get(
        self,
        ip: str,
        ua: str,
        path: str,
        *,
        status: int = 200,
        referer: str = "",
        accept_language: str = "",
        pre: bool = False,
    ) -> bytes | None:
        """Append one document GET to the raw log.

        ``path`` is the full request path, query string included; ``status``
        the true HTTP status of the response; ``referer`` the raw Referer
        header (reduced here to an external https origin, "" when internal
        or absent); ``pre`` marks idle-time preloads from pagerite.js.

        Returns the client hash when the client record was just created (so
        the caller can schedule async enrichment), else None.
        """
        lang, country = _parse_accept_language(accept_language)
        client_hash = _client_hash(ip, ua, lang)
        new = client_hash not in self.data.clients
        if new:
            self._ensure_client(ip, ua, lang, country=country)
        self.data.gets.append(
            Get(
                t=datetime.now(UTC),
                path=path,
                client=client_hash,
                status=status,
                ref=_origin(referer) or "",
                pre=pre,
            )
        )
        self._save()
        return client_hash if new else None

    def record_msg(
        self,
        fr: str,
        to: str | None,
        ip: str,
        ua: str,
        accept_language: str = "",
        hide: bool = False,
        read: int = 0,
    ) -> bytes | None:
        """Append one client activity message (``Ping`` from pagerite.js) to
        the raw log.

        ``to`` is validated here — internal slug path or external https URL,
        anything else is dropped; that is sanitation, not classification.
        No abuse/bot filtering happens at record time: those messages are
        stored raw and filtered at display time, so future rule changes lose
        nothing.  ``hide`` flags the client record as an admin; the message
        itself is recorded normally and hidden at display time like
        everything else the client ever did.

        Returns the client hash when the client record was just created (so
        the caller can schedule async enrichment), else None.
        """
        lang, country = _parse_accept_language(accept_language)
        client_hash = _client_hash(ip, ua, lang)
        new = client_hash not in self.data.clients
        if new:
            self._ensure_client(ip, ua, lang, country=country)
        if hide:
            self.data.clients[client_hash].hide = True
        fr = (_internal_path(fr) or "") if fr else ""
        target = ""
        if to:
            if to.startswith("/") and not to.startswith("//"):
                target = _internal_path(to) or ""
            else:
                target = _external_target(to) or ""
        if target or read > 0:
            self.data.msgs.append(
                Msg(t=datetime.now(UTC), client=client_hash, fr=fr, to=target, read=read)
            )
        if target or read > 0 or hide:
            self._save()
        return client_hash if new else None

    def favicon_origins_needed(self) -> list[str]:
        """External https origins seen in the raw events whose favicon needs
        fetching.

        Covers referer origins of document GETs and external exit targets of
        activity messages — spiders often advertise their own site as the
        referer, so the icon identifies them in the crawler table.  Hidden
        (admin) clients' events never trigger fetches.  Origins with a
        stored icon, or a miss younger than ``_FAVICON_RETRY``, are skipped.
        """
        origins: set[str] = set()
        for g in self.data.gets:
            if g.ref and not self._hidden(g.client):
                origins.add(g.ref)
        for m in self.data.msgs:
            origin = _origin(m.to)
            if origin is not None and not self._hidden(m.client):
                origins.add(origin)
        now = datetime.now(UTC)
        return [
            origin
            for origin in origins
            if (f := self.data.favicons.get(origin)) is None
            or (not f.file and (f.fetched is None or now - f.fetched > _FAVICON_RETRY))
        ]

    def record_favicon(self, origin: str, file: str = "") -> None:
        """Store the favicon fetch result for ``origin`` ("" = miss)."""
        self.data.favicons[origin] = Favicon(file=file, fetched=datetime.now(UTC))
        self._save()

    def display(self, in_menu: Callable[[str], bool] | None = None) -> Display:
        """Build the viewer payload from the raw events.

        All classification happens here, so the stored data is independent
        of the rules:

        - abuse IPs: a 404 on a telltale path (empty segment ``//foo``, dot
          segment or ``*.php`` — but never ``/.well-known/…``, which is
          mostly legitimate browser probing and counts neither as telltale
          nor toward the threshold), or ``_ABUSE_404_THRESHOLD`` plain 404s
          within ``_ABUSE_404_WINDOW`` on paths that don't resolve to a
          menu node (``in_menu``; category placeholders return 404 but are
          real nodes and never count).  Hidden clients never classify.
          Every non-preload GET from such an IP becomes an abuse row; its
          messages are ignored.
        - crawler hits: non-preload GETs no activity message matched within
          ``_CRAWLER_TIMEOUT``, plus visits reclassified as real-browser
          bots (under ``_MIN_VISIT_READ`` seconds of total reading time).
          Messages from bot-UAs are ignored, so their GETs never match.
        - visits: the remaining messages, grouped per client with a new
          visit after ``_SESSION_GAP`` of inactivity.  Trail statuses come
          from the client's GETs (preloads included — a cache-served
          navigation's only GET is its preload); the entry referer and UTM
          tags from the GET that loaded the entry page.

        Hidden (admin) clients are excluded from every list and aggregate.
        """
        in_menu = in_menu or (lambda path: False)
        data = self.data
        ip_of = {h: c.ip for h, c in data.clients.items()}

        # --- abuse classification: each IP's document GETs, chronologically.
        # Hidden (admin) clients never classify: editing means visiting
        # not-found pages (that is where the create pen lives).
        gets_by_ip: dict[str, list[Get]] = {}
        for g in data.gets:
            if not g.pre and not self._hidden(g.client):
                gets_by_ip.setdefault(ip_of.get(g.client, ""), []).append(g)
        menu_cache: dict[str, bool] = {}

        def real_node(path: str) -> bool:
            if path not in menu_cache:
                menu_cache[path] = in_menu(path)
            return menu_cache[path]

        abuse_ips: set[str] = set()
        flag_ids: set[int] = set()
        for ip, gets in gets_by_ip.items():
            gets.sort(key=lambda g: g.t)
            telltale = False
            recent: list[Get] = []  # plain 404s inside the sliding window
            for g in gets:
                if g.status != 404:
                    continue
                path = g.path.split("?")[0]
                if _is_well_known(path):
                    continue  # legitimate probes, never abuse evidence
                if _is_abuse_path(path):
                    telltale = True
                    flag_ids.add(id(g))
                elif not real_node(path):
                    recent.append(g)
                    while g.t - recent[0].t > _ABUSE_404_WINDOW:
                        recent.pop(0)
                    if len(recent) == _ABUSE_404_THRESHOLD:
                        flag_ids.add(id(g))
            if telltale or len(recent) >= _ABUSE_404_THRESHOLD:
                abuse_ips.add(ip)

        # --- per-client event lists, chronological
        msgs_by_client: dict[bytes, list[Msg]] = {}
        for m in data.msgs:
            msgs_by_client.setdefault(m.client, []).append(m)
        for msgs in msgs_by_client.values():
            msgs.sort(key=lambda m: m.t)
        gets_by_client: dict[bytes, list[Get]] = {}
        for g in data.gets:
            gets_by_client.setdefault(g.client, []).append(g)
        for gets in gets_by_client.values():
            gets.sort(key=lambda g: g.t)

        def status_at(client_hash: bytes, path: str, t: datetime) -> int:
            """Latest status served to the client for ``path`` at or before ``t``."""
            gets = gets_by_client.get(client_hash)
            if not gets:
                return 200
            i = bisect_right(gets, t, key=lambda g: g.t)
            for g in reversed(gets[:i]):
                if g.path.split("?")[0] == path:
                    return g.status
            return 200

        def entry_get(client_hash: bytes, path: str, t: datetime) -> Get | None:
            """The GET that loaded ``path`` just before the message at ``t``."""
            gets = gets_by_client.get(client_hash)
            if not gets:
                return None
            i = bisect_right(gets, t, key=lambda g: g.t)
            for g in reversed(gets[:i]):
                if g.t < t - _CRAWLER_TIMEOUT:
                    break
                if not g.pre and g.path.split("?")[0] == path:
                    return g
            return None

        # --- visits: group each visible, non-abuse, non-bot client's messages
        visits: list[Visit] = []
        for h, msgs in msgs_by_client.items():
            if self._hidden(h) or ip_of.get(h, "") in abuse_ips:
                continue
            client = data.clients.get(h)
            if client is not None and _is_bot_ua(client.ua):
                continue
            visit: Visit | None = None
            last_t: datetime | None = None
            for m in msgs:
                if m.to:
                    if visit is None or (
                        last_t is not None and m.t - last_t > _SESSION_GAP
                    ):
                        # First navigation ever, or after a long silence:
                        # start a visit.  The entry referer/UTM tags come
                        # from the GET that loaded the entry page.  Only an
                        # internal page load can open a visit — an external
                        # exit without an open visit is dropped.
                        if not m.to.startswith("/"):
                            last_t = m.t
                            continue
                        visit = Visit(start=m.t, entry=m.to, client=h)
                        g = entry_get(h, m.to, m.t)
                        if g is not None:
                            visit.referer = g.ref
                            visit.utm = _utm_tags(
                                g.path.split("?", 1)[1] if "?" in g.path else ""
                            )
                        visit.trail[m.t] = TrailItem(
                            to=m.to, status=status_at(h, m.to, m.t)
                        )
                        visits.append(visit)
                    else:
                        fr = m.fr or "(direct)"
                        visit.navs[m.t] = Nav(fr=fr, to=m.to)
                        status = status_at(h, m.to, m.t)
                        # First-seen only: repeat pages and repeated exits
                        # update the existing trail item instead of appending.
                        for item in visit.trail.values():
                            if item.to == m.to:
                                item.status = status
                                break
                        else:
                            visit.trail[m.t] = TrailItem(to=m.to, status=status)
                if m.read > 0 and m.fr and visit is not None:
                    for item in visit.trail.values():
                        if item.to == m.fr:
                            item.read += m.read
                            break
                last_t = m.t

        # --- crawler hits: document GETs no message matched
        crawlers: list[CrawlerHit] = []
        msg_times = {h: [m.t for m in msgs] for h, msgs in msgs_by_client.items()}
        for g in data.gets:
            if g.pre or self._hidden(g.client):
                continue
            if ip_of.get(g.client, "") in abuse_ips:
                continue
            client = data.clients.get(g.client)
            if client is None or not _is_bot_ua(client.ua):
                msgs = msgs_by_client.get(g.client, [])
                times = msg_times.get(g.client, [])
                stripped = g.path.split("?")[0]
                matched = False
                for m in msgs[bisect_left(times, g.t) :]:
                    if m.t - g.t > _CRAWLER_TIMEOUT:
                        break
                    if m.to == stripped:
                        matched = True
                        break
                if matched:
                    continue
            entry, _, query = g.path.partition("?")
            crawlers.append(
                CrawlerHit(
                    start=g.t,
                    entry=entry,
                    client=g.client,
                    referer=g.ref,
                    query=query,
                    status=g.status,
                )
            )

        # --- real-browser bots: visits with too little reading time become
        # crawler hits (one per internal trail page) and count nowhere
        kept: list[Visit] = []
        for visit in visits:
            if sum(item.read for item in visit.trail.values()) >= _MIN_VISIT_READ:
                kept.append(visit)
                continue
            query = urlencode(visit.utm)
            first = True
            for t, item in visit.trail.items():
                if not item.to.startswith("/"):
                    continue
                crawlers.append(
                    CrawlerHit(
                        start=t,
                        entry=item.to,
                        client=visit.client,
                        referer=visit.referer if first else "",
                        query=query if first else "",
                        status=item.status,
                    )
                )
                first = False

        display = Display(
            visits=kept,
            crawlers=crawlers,
            abuse=[
                AbuseHit(
                    start=g.t,
                    path=g.path,
                    client=g.client,
                    flag=id(g) in flag_ids,
                    is_404=g.status != 200,
                )
                for g in data.gets
                if not g.pre
                and not self._hidden(g.client)
                and ip_of.get(g.client, "") in abuse_ips
            ],
            clients={h: c for h, c in data.clients.items() if not c.hide},
            favicons={
                origin: f"/_f/{f.file}"
                for origin, f in data.favicons.items()
                if f.file
            },
        )
        for visit in kept:
            bucket = _bucket(visit.start)
            site = display.site_visits
            site[bucket] = site.get(bucket, 0) + 1
            entry_views = display.views.setdefault(visit.entry, {})
            entry_views[bucket] = entry_views.get(bucket, 0) + 1
            fr = visit.referer or "(direct)"
            buckets = display.transitions.setdefault(fr, {}).setdefault(visit.entry, {})
            buckets[bucket] = buckets.get(bucket, 0) + 1
            for t, nav in visit.navs.items():
                nb = _bucket(t)
                if nav.to.startswith("/"):
                    nav_views = display.views.setdefault(nav.to, {})
                    nav_views[nb] = nav_views.get(nb, 0) + 1
                nbuckets = display.transitions.setdefault(nav.fr, {}).setdefault(
                    nav.to, {}
                )
                nbuckets[nb] = nbuckets.get(nb, 0) + 1
        return display

    def display_json(self, in_menu: Callable[[str], bool] | None = None) -> str:
        """The ``display()`` payload as a JSON string for the WebSocket."""
        return msgspec.json.encode(self.display(in_menu)).decode()
