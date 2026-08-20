"""Server-side visit analytics (collection only; see docs/analytics.md).

Events come from navigation pings POSTed to /_a by pagerite.js: the first
ping on page load starts a visit, later pings extend it, and pings with no
known session start a fresh one (missing data, not dropped). The document
GET handler only stashes the entry referer (external https origin) in an
in-memory IP -> referer table, consumed when the ping starts the visit;
nothing is counted without a ping (bots and admin browsing stay invisible).
The session map is in-memory only; IPs are never persisted.

Data is a msgspec Struct JSON-dumped to its own file (not the kanta db),
rewritten atomically on every recorded event.
"""

import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import msgspec


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
    trail: list[str] = []


class Analytics(msgspec.Struct, omit_defaults=True):
    """Root of the analytics JSON file. Append-only by design: old data is
    dropped by deleting list entries / bucket keys."""

    visits: list[Visit] = []
    #: Page transition matrix: from -> to -> count. ``from`` is the referer
    #: origin or "(direct)" for initial loads, a page path for pings.
    transitions: dict[str, dict[str, int]] = {}
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


class Store:
    """In-memory analytics data plus the (IP, UA) -> visit session map."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = Analytics()
        if path.exists():
            try:
                self.data = msgspec.json.decode(path.read_bytes(), type=Analytics)
            except (msgspec.DecodeError, OSError):
                pass  # corrupt/unreadable file: start fresh
        #: (ip, user-agent) -> index of the current visit in data.visits
        self.sessions: dict[tuple[str, str], int] = {}
        #: ip -> external https origin of the latest document GET carrying
        #: one, stashed for the visit the client's initial ping starts.
        #: Internal or absent referers never touch the table.
        self.pending_referers: dict[str, str] = {}

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

    def _count(self, table: dict[str, int], key: str) -> None:
        table[key] = table.get(key, 0) + 1

    def _new_visit(self, entry: str, referer: str, key: tuple[str, str]) -> Visit:
        now = datetime.now(UTC)
        visit = Visit(start=now, entry=entry, referer=referer)
        self.data.visits.append(visit)
        self.sessions[key] = len(self.data.visits) - 1
        self._count(self.data.site_visits, _bucket(now))
        self._count(self.data.views.setdefault(entry, {}), _bucket(now))
        self._count(
            self.data.transitions.setdefault(referer or "(direct)", {}), entry
        )
        return visit

    def entry_referer(self, referer: str, own_origin: str, ip: str) -> None:
        """Stash the entry referer of a document GET for ping attribution.

        Nothing is counted here — the client's initial /_a ping starts the
        visit (only non-admin clients ping). Only a cross-origin https
        referer updates the table; an internal or absent referer leaves any
        stashed origin untouched.
        """
        if not referer:
            return
        origin = _origin(referer)
        if origin is None or origin == own_origin:
            return
        self.pending_referers[ip] = origin

    def ping(self, from_: str, to: str, ip: str, ua: str) -> None:
        """Record a client navigation ping ({from, to} from pagerite.js).

        ``to`` is an internal path ("/...") or an https origin for exit
        links; anything else is ignored. The transition is always counted;
        the trail only grows on first sight of a page within the visit.
        A ping with no known session starts a fresh visit, consuming the
        referer stashed by the document GET if there is one.
        """
        if to.startswith("/") and not to.startswith("//"):
            target = _internal_path(to) or ""
        else:
            target = _origin(to) or ""
        if not target or (not to.startswith("/") and target != to):
            return
        key = (ip, ua)
        index = self.sessions.get(key)
        fr = (_internal_path(from_) or "(direct)") if from_ else "(direct)"
        if index is None or index >= len(self.data.visits):
            # No known session: the initial ping of a fresh page load (or
            # missing data after a server restart) — start a visit.
            visit = self._new_visit(target, self.pending_referers.pop(ip, ""), key)
        else:
            visit = self.data.visits[index]
            now = datetime.now(UTC)
            if target.startswith("/"):
                self._count(self.data.views.setdefault(target, {}), _bucket(now))
            self._count(self.data.transitions.setdefault(fr, {}), target)
            # First-seen only: repeat pages and repeated exits don't append.
            if visit.entry != target and target not in visit.trail:
                visit.trail.append(target)
        self._save()
