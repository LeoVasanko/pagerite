# Analytics

Server-side visit analytics. Data lives in a plain JSON file — a msgspec
Struct dumped to disk — separate from the kanta content database, path from
`PAGERITE_ANALYTICS` (default: `analytics.json` in the per-site data
directory, e.g. `localhost/analytics.json`).

- `pagerite/analytics.py` — data model (`Analytics`, `Client`, `Visit`,
  `CrawlerHit`, `AbuseHit`, `Favicon`) and the `Store` (in-memory data + session map,
  atomic JSON persistence).
- `pagerite/pages.py` — entry-referer stashing in `show_page` (`_track_entry`,
  in `pagerite/tracking.py`), 404 recording.
- `pagerite/tracking.py` — the `/_ws` activity WebSocket, and
  `WebSocket /_api/ws/analytics` (admin-gated like every `/_api` endpoint).
- `frontend/src/pagerite.js` — the client activity channel and the 📊 pen.
- `frontend/src/AnalyticsView.vue` — viewer component rendered inside the
  normal site layout on the `/_a` analytics page.
- `frontend/src/analytics-main.js` — page entry that mounts `AnalyticsView`
  into `#analytics-app` inside `#main`.

## What is collected

The client (`pagerite.js`) keeps a WebSocket connection to `/_ws` for the
whole browsing session and sends activity messages over it — JSON text
frames matching the server's `Ping` msgspec struct with the fields `fr`
(source path), `to` (navigation target), `read` (active seconds on `fr`
since the last report) and `hide`; falsy fields are omitted. One channel
follows the session, so the activity of a visit stays tied together, and
while the user is active the accumulated reading time is flushed every few
seconds: the trail times are cumulative, so a disconnection simply leaves
the last reported time in place (no close beacon). After 5 minutes without
any activity the client closes the socket itself — a sleeping browser tab
would lose it anyway — and the next activity reconnects as a fresh session;
reconnects are attempted only on user activity, with an exponential backoff
between attempts so a failing endpoint is never hammered. Idle-time link preloads
stay plain `fetch()` calls so the browser may cache the responses; the
WebSocket reports actual navigations and active time spent on a page.

- **Initial page load**: only `to` — the loaded path — is sent, never `fr`
  (an `fr` equal to `to` would log a bogus self-transition when a session
  already exists, e.g. a second tab). This message is what starts
  the visit and counts the entry page view — the document GET alone records
  nothing, so bots never register (admin browsing does register, but
  flagged `hide`; see **Admins** below). JS-running crawlers
  (Googlebot, GoogleOther, Applebot, ...) do connect and report, but their
  User-Agent gives them away: messages whose UA matches `_is_bot_ua`
  (anything calling
  itself a "bot", plus known exceptions such as GoogleOther) are ignored
  server-side, and their document GETs land in the crawler list instead.
  Real-browser bots whose UA does not match still register a visit, but
  their reported reading time stays under 5 seconds, so they are
  reclassified as crawler hits at display time (see **Crawler hits** below).
  No source-IP verification is done: a spoofed bot UA merely lands in the
  crawler stats, and scanners that probe telltale paths are caught by the
  abuse rules regardless. Reloads are not
  visits: the message is skipped (PerformanceNavigationTiming `reload`), so a
  refresh neither counts a second view nor logs a self-transition. The GET
  handler stashes a cross-origin https `Referer` (origin part only —
  unavailable to JS once the page has loaded) and any
  `utm_*` query parameters in in-memory IP tables, consumed by the first
  message that
  starts the visit; internal or absent referers never touch the referer table.
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
  the editor
  open (`body.editing`). Admin noise, not visits. Navigating *away* from
  `/_a` does report: the fetch-navigation already GET-ed the target page
  without the preload header, and without the message that GET would flush to
  the crawler list.
- **Admins**: when SSO is in use and the session is known to be an admin,
  the client still reports but adds `hide`. The activity is recorded as
  usual (navigations and all), but the `hide` flag is set on the **client
  record** — so it covers everything that client ever did: visits and
  crawler hits from before the login included. Hidden clients never appear
  in the viewer payload: `Store.display()` drops their visits, crawler
  hits, abuse hits and metadata, and computes every aggregate (site visits,
  page views, transitions) from the visible visits only, so nothing needs
  to be reversed or redacted. Pending crawler hits from a hidden client
  are discarded when they expire, so admin browsing never lands in the
  crawler list either. With no auth proxy
  (dev/test) "admin" is everyone's state, so `hide` stays 0 and everything
  is recorded.
- The server validates `to`: internal paths must be valid slug paths
  ("/" or `[a-z0-9_-]` segments), external ones are re-derived to the
  https origin and accepted only when the client sent exactly that.
- **External-site favicons**: for every external https origin seen as a visit
  referer, a crawler-hit referer or an exit link, the server fetches `{origin}/favicon.ico` in a
  background task (httpx, 8 s timeout, ≤ 64 KB, image content-types only —
  SVG is sniffed from the body when served without an image type) and stores
  the icon content-hashed on disk in the FileStore (served at `/_f/{name}`,
  extension matching the actual MIME).  The origin → file name mapping is
  recorded in `Analytics.favicons` (`Favicon.file`/`fetched`); misses are
  recorded too and retried only after 7 days.  Fetches are scheduled after
  each activity message and once at startup, which backfills icons for already-recorded
  data.  The viewer payload carries `favicons` (origin → `/_f/...` path),
  and the viewer shows the icon wherever an external site is mentioned:
  referer/exit trail links in the visit table and the source/exit pills of
  the transition map (UTM-attributed source nodes without an https origin
  stay text-only).
- **Client records**: the visitor's IP (IPv4 or IPv6 /64 network), raw
  `User-Agent` and extracted `Accept-Language` tag are hashed with blake3;
  the first 6 bytes identify a shared `Client` record.  The `Client` stores
  the full IP, `User-Agent`, compact `ua_pretty`, `lang`, initial
  `country` from the language-region subtag, and asynchronously-filled
  `country`/`city` from DB-IP geoip plus reverse-DNS `host`.  Visits,
  crawler hits and abuse hits all reference this record by its hash, so
  client metadata is stored once instead of repeated per event.
- The visitor IP is stored in the `Client`.  A reverse-DNS lookup is
  attempted for each new client and the result, when available, is stored as
  `host`; local/reserved/multicast addresses are skipped.  If a DB-IP MMDB
  file (`dbip-*.mmdb` or `dbip-*.mmdb.gz`) is present in the repository
  root, it is loaded at startup and used to look up `country`/`city`.  These
  lookups run in background tasks after the event is stored, so WebSocket
  message handling is never delayed.  The decompressed `dbip-*.mmdb` file is kept in
  the repository root and ignored by git.  The CLI flag `--dbip`
  (`uv run pagerite --dbip`) downloads the latest
  `dbip-city-lite-YYYY-MM.mmdb.gz` from DB-IP at startup (in the app
  lifespan, before the MMDB is opened),
  skipping the download when the local database is already current and
  removing older versions after an update; without the flag only an existing
  file is used.
- **Crawler hits**: every document GET is queued in RAM as a pending crawler
  hit — except idle-time link preloads from pagerite.js, which carry an
  `x-pagerite-preload` header and are not tracked at all (the navigation
  message sent when the user actually navigates to a preloaded page does
  the counting; forging
  the header only hides a GET from the crawler stats, the path-based abuse
  classification is unaffected).  If a message
  from the same client arrives within 10 seconds the hit is discarded;
  otherwise it is written to `crawlers` — unless the client is hidden
  (admin), in which case the hit is discarded on expiry too.  Crawlers do not count as
  visits or views.  Bots running real browsers can still slip past the UA
  check: a visit whose total reported reading time stays under 5 seconds
  (`_MIN_VISIT_READ`; durations are client-provided and trusted — such bots
  report 0–2 s) is reclassified as crawler hits at display time, one hit
  per internal trail page, and counts in no visit aggregate.  The
  `Accept-Language` header is stored on the shared
  `Client` immediately; reverse-DNS host names and DB-IP geoip
  country/city are filled in asynchronously, just like for real visits.  In
  the analytics viewer, crawler hits are grouped by client hash and shown as
  a trail of internal pages that crawler visited, preceded by its referer
  when there is one — spiders often advertise their own site as the
  referer, and it is rendered with its favicon like visit referers (crawler
  referers are included in the favicon fetch origins).  The crawler table lists
  the most recent crawler first, with the most active as a tie-breaker.
- **Abuse (scanner) hits**: a 404 for a telltale path — any URL segment
  starting with a dot (`/.env`, `/.git/config`) or ending in `.php` —
  classifies the source IP as abuse immediately, and ten plain 404s from one
  IP do too.  Classification reclassifies history: all earlier crawler hits
  from that IP (persisted and pending) move to the `abuse` list, so a
  random-UA scanner no longer pollutes the crawler stats of the legitimate
  bot it impersonates.  Once classified, every document GET and 404 from the
  IP is recorded as an abuse hit with the full request path (query string
  included), and its activity messages are ignored.  The classified IP set (`abuse_ips`)
  is persisted in the JSON file; the plain-404 counters are RAM-only.  In the
  viewer, abuse hits are grouped by IP (never by client/UA — scanners
  randomize theirs) in a separate "Abuse" table.  Identical paths are
  collapsed into one entry with their hit count.  The 404 probes ("paths
  abused": flagged paths that triggered classification first, then other
  404s) are kept in a separate column from the real articles the abuser
  actually read ("articles read": document GETs that returned 200, not the
  404 fallback rendering — rendered as trail links like the visitor and
  crawler tables, with the query string stripped).  Raw User-Agent strings are shown one
  per line with their occurrence counts, and the full lists are click-to-copy.

## Visits and sessions

There are no cookies. A visit is tied together by a client hash — the first
6 bytes of a blake3 digest over the prettified IP (IPv4 unchanged, IPv6
/64 network), the raw `User-Agent` string and the extracted
`Accept-Language` tag.  The first message from a client hash starts a new
visit; subsequent messages extend it.  Messages arriving with no known session
(server restart) start a fresh visit from the first message — treated as
missing data rather than dropped.  The client-hash → visit map and the IP →
entry-referer/UTM tables are in-memory only; client metadata is stored in
`Analytics.clients` keyed by the client hash.

Each `Client` record:

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
- `hide` — true for admin clients (`hide` message field): all their visits,
  crawler hits and abuse hits are recorded but excluded from every
  statistic and from the viewer payload.

Each `Visit` record:

- `start` — timestamp of the first event,
- `entry` — first page (path) seen,
- `referer` — external https origin of the initial load, `""` for direct,
- `client` — 6-byte blake3 hash referencing `Analytics.clients`,
- `trail` — the entry page and everything seen afterwards, keyed by the
  timestamp of first sight (insertion order = first-seen order). Each item
  holds `to` (page path or external exit URL), the accumulated active
  reading time in seconds (`read`) and the most recent HTTP status seen
  for the target (`status`). Re-visiting an already seen target updates
  its item instead of appending.
- `navs` — every navigation message (`fr`, `to`), keyed by its timestamp,
  repeats included. The aggregates are computed from this log at display
  time.
- `utm` — `utm_*` query parameters from the landing URL, as a dict.

Each `CrawlerHit` record:

- `start` — timestamp of the document GET,
- `entry` — page path requested,
- `client` — 6-byte blake3 hash referencing `Analytics.clients`,
- `referer` — external https origin of the request, `""` for direct/none,
- `query` — raw query string of the request,
- `status` — HTTP status of the served response (200 for a real page, 404
  for a category placeholder or missing page).

Each `AbuseHit` record:

- `start` — timestamp of the request,
- `path` — full request path including the query string (e.g. `/.env?x=1`),
- `client` — 6-byte blake3 hash referencing `Analytics.clients`,
- `flag` — true for the path that triggered abuse classification (telltale
  path or the 404 that crossed the threshold),
- `is_404` — true for 404 responses (probed paths and 404-fallback document
  GETs), false for real (200) document GETs — articles the abuser read.

Crawler hits are grouped by client hash in the analytics viewer; abuse hits
are grouped by IP alone (resolved from the referenced `Client`).  In the
Abuse table identical paths are collapsed with their counts, split into the
404 probes (flagged paths that triggered classification first, then other
404s, shown verbatim) and the 200 document GETs shown as trail links in the
separate articles column.
Within each list paths are
sorted by count descending, then by their earliest hit.

In the visitor and crawler tables, internal paths that returned a 404 status
are shown in red and the link title includes the status code, so it is easy
to tell misses from real pages at a glance.

## Aggregates

Aggregates are **not stored**; they are computed at display time by
`Store.display()` from the visit records (entry + `navs` log), skipping
hidden clients' visits and short visits reclassified as crawler hits
(under `_MIN_VISIT_READ` seconds of total reported reading time). This is
what allows a client to become hidden after
navigations were already logged: no counts need reversing. The computed
shapes, part of the WebSocket payload (`Display` struct alongside `visits`,
`crawlers`, `abuse` and `clients`):

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
list entries (`visits` is a plain append-only list).

## Persistence

The whole `Analytics` struct is JSON-encoded and written atomically
(temp file + rename) on every recorded event. Traffic on a small CMS makes
this cheap enough; batching can be added later without changing the format.

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
