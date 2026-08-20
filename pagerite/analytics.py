"""Server-side visit analytics (collection only; see docs/analytics.md).

Events come from navigation pings POSTed to /_a by pagerite.js: the first
ping on page load starts a visit, later pings extend it, and pings with no
known session start a fresh one (missing data, not dropped). The document
GET handler stashes the entry referer (external https origin) and any
utm_* query parameters in in-memory IP tables, consumed when the ping
starts the visit; nothing is counted without a ping (bots and admin
browsing stay invisible). The session map is in-memory only.  The visitor
IP and, when available, its reverse-DNS host name are stored on the visit
record itself.

Data is a msgspec Struct JSON-dumped to its own file (not the kanta db),
rewritten atomically on every recorded event.
"""

import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
    browser = browser if browser and browser != "Other" else ""
    os_name = os_name if os_name and os_name != "Other" else ""
    if dev in (None, "Other") or dev == browser:
        dev = ""
    parts = [f"{browser}/{ver}" if browser else "", os_name, dev]
    return " ".join(p for p in parts if p).strip()


class Visit(msgspec.Struct, omit_defaults=True):
    """One visit: the initial-load data plus everything seen afterwards.

    ``trail`` holds page paths and external exit origins in first-seen
    order; re-visiting an already seen page does not append. The entry
    page itself is in ``entry``, not in the trail.
    """

    start: datetime
    entry: str
    #: External https origin of the initial load, "" for direct visits.
    referer: str = ""
    #: Visitor IP address (first X-Forwarded-For hop or direct peer).
    ip: str = ""
    #: Reverse-DNS host name for ``ip`` when resolvable, else "".
    host: str = ""
    trail: list[str] = []
    #: First Accept-Language tag, lowercased (e.g. "en-us").
    lang: str = ""
    #: Two-letter region subtag derived from ``lang`` (e.g. "US"), or "".
    country: str = ""
    #: Raw User-Agent header from the initial ping.
    ua: str = ""
    #: Compact display form of ``ua`` (browser/OS/device) when parsable.
    ua_pretty: str = ""
    #: UTM query parameters from the landing URL, keyed by parameter name.
    utm: dict[str, str] = {}


class CrawlerHit(msgspec.Struct, omit_defaults=True):
    """A document GET that was never followed by an analytics ping."""

    start: datetime
    entry: str
    ip: str = ""
    ua: str = ""
    #: Compact display form of ``ua`` when parsable.
    ua_pretty: str = ""
    #: External https origin of the initial load, "" for direct/none.
    referer: str = ""
    #: Raw query string of the landing URL (UTM tags can be parsed from it).
    query: str = ""


class Analytics(msgspec.Struct, omit_defaults=True):
    """Root of the analytics JSON file. Append-only by design: old data is
    dropped by deleting list entries / bucket keys."""

    visits: list[Visit] = []
    #: Document GETs that never produced a ping, treated as crawler/bot hits.
    crawlers: list[CrawlerHit] = []
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


class Store:
    """In-memory analytics data plus the (IP, UA) -> visit session map."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = Analytics()
        if path.exists():
            try:
                self.data = msgspec.json.decode(path.read_bytes(), type=Analytics)
            except msgspec.DecodeError, OSError:
                pass  # legacy schema / corrupt or unreadable file: start fresh
        #: (ip, user-agent) -> index of the current visit in data.visits
        self.sessions: dict[tuple[str, str], int] = {}
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

    def _flush_crawlers(self, now: datetime | None = None) -> None:
        """Move expired pending crawler hits into persistent ``data.crawlers``."""
        if not self.pending_crawlers:
            return
        now = now or datetime.now(UTC)
        cutoff = now - _CRAWLER_TIMEOUT
        expired: list[CrawlerHit] = []
        remaining: list[CrawlerHit] = []
        for hit in self.pending_crawlers:
            (expired if hit.start <= cutoff else remaining).append(hit)
        if expired:
            self.pending_crawlers = remaining
            self.data.crawlers.extend(expired)
            self._save()

    def _count(self, table: dict[str, int], key: str) -> None:
        table[key] = table.get(key, 0) + 1

    def _count_transition(self, fr: str, to: str, now: datetime) -> None:
        """Count one transition in its 5-minute bucket (sparse matrix)."""
        buckets = self.data.transitions.setdefault(fr, {}).setdefault(to, {})
        self._count(buckets, _bucket(now))

    def _new_visit(
        self,
        entry: str,
        referer: str,
        key: tuple[str, str],
        ip: str = "",
        lang: str = "",
        country: str = "",
        ua: str = "",
        utm: dict[str, str] | None = None,
    ) -> Visit:
        now = datetime.now(UTC)
        visit = Visit(
            start=now,
            entry=entry,
            referer=referer,
            ip=ip,
            lang=lang,
            country=country,
            ua=ua,
            ua_pretty=_compact_user_agent(ua),
            utm=utm or {},
        )
        self.data.visits.append(visit)
        self.sessions[key] = len(self.data.visits) - 1
        self._count(self.data.site_visits, _bucket(now))
        self._count(self.data.views.setdefault(entry, {}), _bucket(now))
        self._count_transition(referer or "(direct)", entry, now)
        return visit

    def enrich_visit(
        self,
        index: int,
        *,
        host: str = "",
        country: str = "",
    ) -> None:
        """Fill in host/geoip fields on an existing visit after async lookups."""
        if index < 0 or index >= len(self.data.visits):
            return
        visit = self.data.visits[index]
        changed = False
        if host and not visit.host:
            visit.host = host
            changed = True
        if country:
            visit.country = country
            changed = True
        if changed:
            self._save()

    def track_entry(
        self,
        referer: str,
        own_origin: str,
        ip: str,
        ua: str,
        entry: str,
        query: str = "",
    ) -> None:
        """Stash the entry referer/UTM tags and queue a pending crawler hit.

        Nothing is counted here — the client's initial /_a ping starts the
        visit (only non-admin clients ping). Only a cross-origin https
        referer updates the table; an internal or absent referer leaves any
        stashed origin untouched.  UTM parameters are kept only when the
        landing URL actually carries them, so a subsequent parameter-less page
        does not erase an earlier tagged landing.

        Every document GET is also queued as a pending crawler hit.  If a ping
        from the same (IP, UA) pair arrives within ``_CRAWLER_TIMEOUT``, the
        hit is discarded; otherwise it is flushed to ``data.crawlers``.
        """
        now = datetime.now(UTC)
        self._flush_crawlers(now)
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
                ip=ip,
                ua=ua,
                ua_pretty=_compact_user_agent(ua),
                referer=self.pending_referers.get(ip, ""),
                query=query,
            )
        )

    def ping(
        self,
        from_: str,
        to: str,
        ip: str,
        ua: str,
        accept_language: str = "",
    ) -> int | None:
        """Record a client navigation ping ({from, to} from pagerite.js).

        ``to`` is an internal path ("/...") or an https origin for exit
        links; anything else is ignored. The transition is always counted;
        the trail only grows on first sight of a page within the visit.
        A ping with no known session starts a fresh visit, consuming the
        referer and UTM tags stashed by the document GET if there are any.

        Returns the index of the new visit when one is created, so callers
        can enrich it later with non-blocking lookups (host, geoip country).
        """
        self._flush_crawlers()
        # A real visitor ping cancels any pending crawler hits from this
        # (IP, UA) pair.
        self.pending_crawlers = [
            hit for hit in self.pending_crawlers if not (hit.ip == ip and hit.ua == ua)
        ]
        if to.startswith("/") and not to.startswith("//"):
            target = _internal_path(to) or ""
        else:
            target = _origin(to) or ""
        if not target or (not to.startswith("/") and target != to):
            return None
        key = (ip, ua)
        index = self.sessions.get(key)
        fr = (_internal_path(from_) or "(direct)") if from_ else "(direct)"
        if index is None or index >= len(self.data.visits):
            # No known session: the initial ping of a fresh page load (or
            # missing data after a server restart) — start a visit.
            lang, country = _parse_accept_language(accept_language)
            index = len(self.data.visits)
            self._new_visit(
                target,
                self.pending_referers.pop(ip, ""),
                key,
                ip=ip,
                lang=lang,
                country=country,
                ua=ua,
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
        return index if index is not None and index < len(self.data.visits) else None
