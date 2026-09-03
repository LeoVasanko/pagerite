# Analytics

Server-side visit analytics built on a **raw access-log-style event store**.
Data lives in a plain JSON file — a msgspec Struct dumped to disk — separate
from the kanta content database, path from `PAGERITE_ANALYTICS` (default:
`analytics.json` in the per-site data directory, e.g. `localhost/analytics.json`).

- `pagerite/analytics.py` — data model (`Analytics`, `Get`, `Msg`, `Client`,
  `Favicon`), the `Store` (raw log + atomic JSON persistence) and
  `Store.display()`, where **all** classification happens.
- `pagerite/pages.py` — records every served document as one raw GET line
  (`_record_get`, in `pagerite/tracking.py`) with its true HTTP status.
- `pagerite/tracking.py` — the `/_ws` activity WebSocket, and
  `WebSocket /_api/ws/analytics` (admin-gated like every `/_api` endpoint).
- `frontend/src/pagerite.js` — the client activity channel and the 📊 pen.
- `frontend/src/AnalyticsView.vue` — viewer component rendered inside the
  normal site layout on the `/_a` analytics page.
- `frontend/src/analytics-main.js` — page entry that mounts `AnalyticsView`
  into `#analytics-app` inside `#main`.

## Raw records

The store is deliberately close to an access log: two append-only lists plus
shared metadata. **Nothing is classified when recorded** — whether a client
turns out to be a reader, a crawler or a scanner is decided by
`Store.display()` from the raw events, so the stored data survives any future
change to the classification rules.

Each `Get` record (one per served document):

- `t` — timestamp of the request,
- `path` — full request path, query string included (e.g. `/.env?x=1`),
- `status` — the true HTTP status of the response (200, or 404 for a category
  placeholder or a missing page),
- `ref` — external https origin of the `Referer`, `""` for direct/internal
  (same-origin referers are dropped by the recorder),
- `pre` — true for idle-time link preloads from pagerite.js
  (`x-pagerite-preload` header): never counted as a view, crawler hit or
  abuse — recorded only so a navigation later served from the in-memory page
  cache (which issues no GET at all) can be attributed this GET's status,
- `client` — 6-byte blake3 hash referencing `Analytics.clients`.

304 revalidation responses return before recording and are not logged.

Each `Msg` record (one per pagerite.js activity message over `/_ws`):

- `t` — timestamp,
- `client` — 6-byte blake3 hash referencing `Analytics.clients`,
- `fr` — path of the page the activity happened on (`""` for the initial
  load),
- `to` — navigation target (validated at record time: internal slug path or
  external https URL; anything else is dropped — sanitation, not
  classification),
- `read` — active seconds spent on `fr` since the previous report.

Each `Client` record (shared by every event, keyed by hash):

- `ip` — visitor IP address (first `X-Forwarded-For` hop, or direct peer),
- `host` — reverse-DNS host name for `ip` when resolvable, else `""`,
- `lang` — first `Accept-Language` tag, lowercased (e.g. `"en-us"`),
- `country` — two-letter country code.  Initially derived from the
  `Accept-Language` region subtag, but overwritten by the DB-IP MMDB result
  when a database is available,
- `city` — city name from the DB-IP MMDB lookup, when available,
- `ua` — raw `User-Agent` string,
- `ua_pretty` — compact display form of the UA (browser/OS/device) when
  parsable, otherwise the raw string,
- `hide` — true for admin clients (`hide` message field): everything this
  client ever did is recorded but excluded from every statistic and from the
  viewer payload.  This is the one flag set at record time — it is a client
  property, not a classification.

A reverse-DNS lookup is attempted for each new client and the result, when
available, is stored as `host`; local/reserved/multicast addresses are
skipped.  If a DB-IP MMDB file (`dbip-*.mmdb` or `dbip-*.mmdb.gz`) is present
in the repository root, it is loaded at startup and used to look up
`country`/`city`.  These lookups run in background tasks after the event is
stored, so WebSocket message handling is never delayed.  The decompressed
`dbip-*.mmdb` file is kept in the repository root and ignored by git.  The
CLI flag `--dbip` (`uv run pagerite --dbip`) downloads the latest
`dbip-city-lite-YYYY-MM.mmdb.gz` from DB-IP at startup (in the app lifespan,
before the MMDB is opened), skipping the download when the local database is
already current and removing older versions after an update; without the flag
only an existing file is used.

## What the client sends

The client (`pagerite.js`) keeps a WebSocket connection to `/_ws` for the
whole browsing session and sends activity messages over it — JSON text
frames matching the server's `Ping` msgspec struct with the fields `fr`
(source path), `to` (navigation target), `read` (active seconds on `fr`
since the last report) and `hide`; falsy fields are omitted. One channel
follows the session, so the activity of a visit stays tied together, and
while the user is active the accumulated reading time is flushed every few
seconds: the times are incremental, so a disconnection simply leaves the
last reported time in place (no close beacon). After 5 minutes without
any activity the client closes the socket itself — a sleeping browser tab
would lose it anyway — and the next activity reconnects; reconnects are
attempted only on user activity, with an exponential backoff between
attempts so a failing endpoint is never hammered. Idle-time link preloads
stay plain `fetch()` calls so the browser may cache the responses; the
WebSocket reports actual navigations and active time spent on a page.

- **Initial page load**: only `to` — the loaded path — is sent, never `fr`
  (an `fr` equal to `to` would log a bogus self-transition when a session
  already exists, e.g. a second tab). Reloads are not
  visits: the message is skipped (PerformanceNavigationTiming `reload`), so a
  refresh neither counts a second view nor logs a self-transition.
- **Internal fetch-navigations**: `to` is the target path, sent only after
  the swap actually happened (a failed swap falls back to a full load,
  whose initial message counts the view instead — no gap, no double count).
- **External links** (`https` only): `to` is the link's full URL. This is the
  exit-link record; the user may continue navigating afterwards (new tab,
  back), so the exit URL is not necessarily the last trail entry. Outbound
  links are stored by full URL so several links to the same domain remain
  distinct.
- **Excluded**: back/forward (popstate) navigations, navigating *to* the
  analytics page (`/_a` — its GET is untracked, and the server cannot
  record it as a navigation target anyway), and everything while the user has
  the editor open (`body.editing`). Admin noise, not visits. Navigating
  *away* from `/_a` does report.
- **Admins**: when SSO is in use and the session is known to be an admin,
  the client still reports but adds `hide`. The activity is recorded as
  usual (navigations and all), but the `hide` flag is set on the **client
  record** — so it covers everything that client ever did, including the
  time before the login. Hidden clients never appear in the viewer payload:
  `Store.display()` drops their events and metadata, and computes every
  aggregate (site visits, page views, transitions) from the visible visits
  only, so nothing needs to be reversed or redacted. With no auth proxy
  (dev/test) "admin" is everyone's state, so `hide` stays 0 and everything
  is recorded.
- **External-site favicons**: for every external https origin seen as a GET
  referer or an exit link, the server fetches `{origin}/favicon.ico` in a
  background task (httpx, 8 s timeout, ≤ 64 KB, image content-types only —
  SVG is sniffed from the body when served without an image type) and stores
  the icon content-hashed on disk in the FileStore (served at `/_f/{name}`,
  extension matching the actual MIME).  The origin → file name mapping is
  recorded in `Analytics.favicons` (`Favicon.file`/`fetched`); misses are
  recorded too and retried only after 7 days.  Fetches are scheduled after
  each activity message and once at startup, which backfills icons for
  already-recorded data.  The viewer payload carries `favicons` (origin →
  `/_f/...` path), and the viewer shows the icon wherever an external site
  is mentioned: referer/exit trail links in the visit table and the
  source/exit pills of the transition map (UTM-attributed source nodes
  without an https origin stay text-only).

## Display-time classification

`Store.display(in_menu)` derives the viewer payload from the raw events on
every (debounced) broadcast — O(n log n) over the log, cheap enough for a
small CMS. `in_menu(path)` resolves a path against the current menu (passed
in from `tracking.py`, which owns the content database import) so 404
responses for real menu nodes — category placeholders — are not mistaken
for misses.

- **Visits and sessions**: a client's messages are grouped into visits
  chronologically; a new visit starts after 30 minutes of inactivity
  (`_SESSION_GAP`). A fresh page load with an already-open visit (second
  tab) extends it, logging a `(direct)` transition. The visit's trail holds
  first-seen targets in order; `read` updates accumulate active seconds on
  the trail item matching `fr`. Each trail item's HTTP status comes from
  the client's latest GET for that path — preloads included, which is what
  allows 404 pages to render red in the viewer even when the navigation
  itself was served from the page cache. The entry page's referer and
  `utm_*` tags come from the GET that loaded it (within 10 s before the
  first message).
- **Crawler hits**: a document GET no activity message matched within
  `_CRAWLER_TIMEOUT` (10 s) is a crawler hit — plain bots that only fetch
  documents never register as visits. JS-running crawlers (Googlebot,
  GoogleOther, Applebot, ...) do connect and send messages, but their UA
  gives them away (`_is_bot_ua`): their messages are ignored at display
  time, so their GETs never match and land in the crawler list too. Real-
  browser bots whose UA does not match are caught by engagement: a visit
  whose total reported reading time is under 5 seconds (`_MIN_VISIT_READ`;
  durations are client-provided and trusted — such bots report 0–2 s) is
  reclassified as crawler hits, one per internal trail page, and counts in
  no visit aggregate. No source-IP verification is done: a spoofed bot UA
  merely lands in the crawler stats, and scanners that probe telltale paths
  are caught by the abuse rules regardless. In the viewer, crawler hits are
  grouped by client hash and shown as a trail of pages, preceded by the
  referer when there is one (rendered with its favicon like visit
  referers). The crawler table lists the most recent crawler first, with
  the most active as a tie-breaker.
- **Abuse (scanner) hits**: a 404 on a telltale path — an empty URL segment
  (`//foo` — no real client generates those), any segment starting with a
  dot (`/.env`, `/.git/config`) or ending in `.php` — classifies the source
  IP as abuse, and ten plain 404s within one hour (`_ABUSE_404_WINDOW`) on
  paths that don't resolve to a menu node do too. Two exemptions keep
  legitimate traffic out: RFC 8615 well-known URIs (`/.well-known/…` —
  browsers and services probe them, e.g. Chrome's devtools fetch of
  `appspecific/com.chrome.devtools.json`) are never telltale and never
  count toward the threshold, and category placeholders return 404 but are
  real menu nodes, so they never count either. The window keeps a
  long-time reader's slowly accumulating misses from ever crossing the
  threshold — scanners spray in bursts. Hidden (admin) clients never
  trigger classification: editing means visiting not-found pages, since
  that is where the create pen lives. Once an IP is classified, **all** its document GETs are shown in the abuse list —
  including any that arrived before classification, since the raw log keeps
  everything — and its activity messages are ignored. In the viewer, abuse
  hits are grouped by IP (never by client/UA — scanners randomize theirs)
  in a separate "Abuse" table, split by the recorded status: the 404 probes
  ("paths abused" — flagged paths that triggered classification first, then
  other 404s, shown verbatim with query strings) versus the real articles
  the abuser actually read ("articles read" — the 200 document GETs,
  rendered as trail links like the visitor and crawler tables, query string
  stripped). Raw User-Agent strings are shown one per line with their
  occurrence counts, and the full lists are click-to-copy.

In the visitor and crawler tables, internal paths that returned a 404 status
are shown in red and the link title includes the status code, so it is easy
to tell misses from real pages at a glance.

## Derived shapes (the viewer payload)

The `Display` payload contains the derived `visits`, `crawlers` and `abuse`
rows (structs `Visit`/`Nav`/`TrailItem`, `CrawlerHit`, `AbuseHit` — display
DTOs only, never persisted), the visible `clients`, the fetched `favicons`,
and the aggregates below.

Each derived `Visit`:

- `start` — timestamp of the first activity,
- `entry` — first page (path) seen,
- `referer` — external https origin of the entry GET, `""` for direct,
- `client` — 6-byte blake3 hash referencing `Analytics.clients`,
- `trail` — the entry page and everything seen afterwards, keyed by the
  timestamp of first sight (insertion order = first-seen order). Each item
  holds `to` (page path or external exit URL), the accumulated active
  reading time in seconds (`read`) and the most recent HTTP status seen
  for the target (`status`),
- `navs` — every navigation (`fr`, `to`), keyed by its timestamp, repeats
  included. The aggregates are computed from this log,
- `utm` — `utm_*` query parameters from the landing URL, as a dict.

Each derived `CrawlerHit`:

- `start` — timestamp of the document GET,
- `entry` — page path requested,
- `client` — 6-byte blake3 hash referencing `Analytics.clients`,
- `referer` — external https origin of the request, `""` for direct/none,
- `query` — raw query string of the request,
- `status` — HTTP status of the served response (200 for a real page, 404
  for a category placeholder or missing page).

Each derived `AbuseHit`:

- `start` — timestamp of the request,
- `path` — full request path including the query string,
- `client` — 6-byte blake3 hash referencing `Analytics.clients`,
- `flag` — true for the paths that triggered abuse classification (telltale
  paths, or the 404 that crossed the threshold),
- `is_404` — true for 404 responses, false for real (200) document GETs.

Crawler hits are grouped by client hash in the analytics viewer; abuse hits
are grouped by IP alone (resolved from the referenced `Client`). In the
Abuse table identical requests (same path and status class) are collapsed
with their counts — a path's 404 probes and its later 200 reads never
merge. Within each list paths are sorted by count descending, then by their
earliest hit.

## Aggregates

Aggregates are **not stored**; they are computed at display time by
`Store.display()` from the derived visits (entry + `navs` log), skipping
hidden clients and short visits reclassified as crawler hits. This is
what allows a client to become hidden after navigations were already
logged: no counts need reversing. The computed shapes, part of the
WebSocket payload (`Display` struct alongside `visits`, `crawlers`, `abuse`
and `clients`):

- `transitions`: time series of page transitions, sparse nested dict
  `from -> to -> bucket -> count` with 5-minute bucketing. `from` is the
  referer origin or `"(direct)"` for initial loads, a page path for
  navigations.
- `views`: time series of page loads, `path -> bucket -> count`, sparse: only
  non-zero 5-minute buckets exist (bucket key is its floored ISO timestamp).
  Every load counts, including repeats within a visit; external exit origins
  are not page views and are not counted here.
- `site_visits`: `bucket -> count` of new visits started, same sparse
  5-minute bucketing.

Sparseness keeps quiet sites small; dropping old data is a matter of deleting
list entries (`gets`/`msgs` are plain append-only lists).

## Persistence

The whole `Analytics` struct is JSON-encoded and written atomically
(temp file + rename) on every recorded event. Traffic on a small CMS makes
this cheap enough; batching can be added later without changing the format.
A file written by the pre-redesign schema (stored `visits`/`crawlers`/`abuse`
lists) is not convertible; it is renamed to `analytics.json.bak-legacy` and
recording starts fresh.

## Viewing

The 📊 pen in the banner corner (admins only, injected by pagerite.js next to
the edit pens) links to `/_a`, the analytics page. It is a normal site page:
the standard banner, navigation and footer stay in place, and the analytics
content is rendered inside `#main`. The page itself is public, but the data
stream comes from `WebSocket /_api/ws/analytics`, which remains admin-gated
like the rest of the management API; visitors without access see the viewer
with a "could not be loaded" message.

Because it is a real page, fetch-navigation handles it like any other internal
link: clicking the 📊 pen (or any link to `/_a`) fetches the server-rendered
HTML, swaps the dynamic regions and mounts the Vue analytics app in place. The
range selector updates the URL hash (`#week` etc.) so links to a specific
range can be shared. When the URL has no hash, the client derives the
default from the first analytics snapshot: `day` if the recorded history
spans less than 24 hours, otherwise `week`.

`AnalyticsView.vue` is no longer a full-screen overlay; the `body.analytics-open`
page-chrome hiding and `#/analytics/<range>` hash routing have been removed.

Charts are SVG curves (Catmull-Rom over an edge-aware Gaussian — a
change-point detector splits the series at traffic-level shifts, then each
segment is smoothed independently with a fixed sigma chosen so N events in
a single bucket peak at N events per unit. The raw series is drawn faint
underneath). Values are
**per-unit rates** — per hour on the week view (5-minute bucket counts × 12,
plotted at native 5-minute resolution), per day on the month+ ranges — and
the smoothing time scale follows the unit: the month+ sigmas are 24× the
hourly ones. The y max is derived from the smoothed curves so single-bucket
spikes don't blow up the scale, and raw spikes are clamped into the plot.
Axes always start at 0 and end at a multiple of a 1-2-5 major step (max 5
labeled intervals, minor lines at fifths when integral; the minimum y-axis
range is 10 so tiny values such as a single visit are not stretched to a
fractional scale).
The week range is aligned to Monday 00:00 UTC and overlays up to 8 previous
weeks in the muted color at decreasing opacity (the current week keeps the
accent color and is
truncated at the current bucket, never drawing fake zeroes for the future);
a compact legend inside the top right of the visits chart marks the current
ISO week in accent and the overlaid past weeks as "Week M" or "Week M–N" on
a muted specimen. Its x labels are weekday names centered at midday UTC, without
vertical grid
lines (day boundaries would be misleading in the viewer's timezone). The
month view labels days the same lineless way — day numbers at noon UTC,
with the month name substituted for the 1st. Month, year and all are
rolling windows ending at now, aligned to UTC day boundaries at the start
so the labels span the whole range; the bucket size follows the window —
6 hours up to 31 days, daily beyond — with boundary lines at months/years
on the longer ranges.  All uses the full data reach, but keeps
at least the past 30 days (identical to the month view when the site is
younger than that, bucket size included) so the chart never collapses to a
tiny sliver when the site is young. Below the charts: a **transition map** (all pages from
`/_api/pages` — top-level menu items on a large-radius circular arc whose
bottom point is the last item (each earlier item a bit higher), connected
by a top lane labeled 🏠︎ beside the home pill (50% thicker than
the branch lanes, its label font and guide offset scaled along), each item's
subtree fanning out below it in menu order along a large-radius circular
arc that leaves heading
straight down and gradually bends right, index pages without views omitted
and their children promoted in their place. The submenu structure is drawn
as wide branch lanes: one per path prefix with at least two visible
nodes, running behind the branch's node pills as circle arcs concentric
with the fan (parent levels one radius step outward, so all lanes of a
group share exactly one form), each labeled with its branch slug
left-aligned just past the first pill and allowed to run along the lane to
its end, disappearing under later pills when long — so the lanes reflect
the path
structure even where index pages are omitted — opposite transition
directions joined into organic
tapered connections whose middle width grows logarithmically (base 2)
with the daily hit rate (uncapped), connections
carrying less than 1% of the total traffic
pruned, as are those whose thin middle would render below ~0.8 px —
fainter strands are invisible and only their wide end flares would show; beads are simulated one by one in JS (requestAnimationFrame) and
flow along each edge, persisting across data reloads (emitters are keyed
per edge direction and beads tracked by progress, so an unrelated count
change never reshuffles them), emitted at a rate linearly proportional
to the directional count with no in-flight limit, opposing directions
offset onto parallel lanes. External sources and exits whose connectors are
all culled by the width threshold are dropped from their rows themselves
(the site's own page nodes always stay, connected or not). External sources show as a node row above the
map: each visit is attributed to `utm_campaign`, then `utm_source`, then the
referer origin, then any other `utm_*` tag, so UTM-tagged visits are grouped
under their campaign/source value rather than the referer domain. A UTM
source node only links to its referer when every visit carrying that tag
came from the same origin. External exits are full-size nodes in a matching
row centered below the map, so the site itself stays in the middle), per-page view
counts, the top transitions and the 50 most recent visit trails. Data is
streamed live over `WebSocket /_api/ws/analytics`, which pushes the latest
JSON snapshot on connect and again whenever the analytics file is updated
(with a small server-side debounce to avoid flooding under high traffic).
