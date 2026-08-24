# Analytics

Server-side visit analytics. Data lives in a plain JSON file — a msgspec
Struct dumped to disk — separate from the kanta content database, path from
`PAGERITE_ANALYTICS` (default: the database path with `.kantadb` replaced by
`.analytics.json`, e.g. `pagerite.analytics.json`).

- `pagerite/analytics.py` — data model (`Analytics`, `Client`, `Visit`,
  `CrawlerHit`, `AbuseHit`) and the `Store` (in-memory data + session map,
  atomic JSON persistence).
- `pagerite/app.py` — entry-referer stashing in `show_page` (`_track_entry`),
  the `POST /_a` ping endpoint, and `WebSocket /_api/ws/analytics`
  (admin-gated like every `/_api` endpoint).
- `frontend/src/pagerite.js` — client navigation pings and the 📊 pen.
- `frontend/src/AnalyticsView.vue` — viewer component rendered inside the
  normal site layout on the `/_a` analytics page.
- `frontend/src/analytics-main.js` — page entry that mounts `AnalyticsView`
  into `#analytics-app` inside `#main`.

## What is collected

The client (`pagerite.js`) POSTs fire-and-forget pings to `/_a` with
`{fr, to}` (`fr` = source path):

- **Initial page load**: `to` is the loaded path. This ping is what starts
  the visit and counts the entry page view — the document GET alone records
  nothing, so bots and admin browsing never register. JS-running crawlers
  (Googlebot, GoogleOther, Applebot, ...) do ping, but their User-Agent
  gives them away: pings whose UA matches `_is_bot_ua` (anything calling
  itself a "bot", plus known exceptions such as GoogleOther) are ignored
  server-side, and their document GETs land in the crawler list instead.
  No source-IP verification is done: a spoofed bot UA merely lands in the
  crawler stats, and scanners that probe telltale paths are caught by the
  abuse rules regardless. Reloads are not
  visits: the ping is skipped (PerformanceNavigationTiming `reload`), so a
  refresh neither counts a second view nor logs a self-transition. The GET
  handler stashes a cross-origin https `Referer` (origin part only) and any
  `utm_*` query parameters in in-memory IP tables, consumed by the ping that
  starts the visit; internal or absent referers never touch the referer table.
- **Internal fetch-navigations**: `to` is the target path, sent only after
  the swap actually happened (a failed swap falls back to a full load,
  whose initial ping counts the view instead — no gap, no double count).
- **External links** (`https` only): `to` is the link's full URL. This is the
  exit-link record; the user may continue navigating afterwards (new tab,
  back), so the exit URL is not necessarily the last trail entry. Outbound
  links are stored by full URL so several links to the same domain remain
  distinct.
- **Excluded**: back/forward (popstate) navigations, navigating *to* the
  analytics page (`/_a` — its GET is untracked, and the server rejects it
  as a ping target anyway), and everything while the user has the editor
  open (`body.editing`). Admin noise, not visits. Navigating *away* from
  `/_a` does ping: the fetch-navigation already GET-ed the target page
  without the preload header, and without the ping that GET would flush to
  the crawler list.
- **Admins**: when SSO is in use and the session is known to be an admin,
  the client still pings but adds `hide=1`. The server then records
  nothing — and if the same client session already had a visit from before
  logging in, that visit is removed from the JSON along with every count
  it recorded — an in-memory per-visit log of count events makes full
  reversal possible. With no auth proxy (dev/test)
  "admin" is everyone's state, so `hide` stays 0 and everything is recorded.
- The server validates `to`: internal paths must be valid slug paths
  ("/" or `[a-z0-9_-]` segments), external ones are re-derived to the
  https origin and accepted only when the client sent exactly that.
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
  lookups run in background tasks after the event is stored, so the `/_a`
  response is never delayed.  The decompressed `dbip-*.mmdb` file is kept in
  the repository root and ignored by git.  The CLI flag `--dbip`
  (`uv run pagerite --dbip`) downloads the latest
  `dbip-city-lite-YYYY-MM.mmdb.gz` from DB-IP before the server starts,
  skipping the download when the local database is already current and
  removing older versions after an update; without the flag only an existing
  file is used.
- **Crawler hits**: every document GET is queued in RAM as a pending crawler
  hit — except idle-time link preloads from pagerite.js, which carry an
  `x-pagerite-preload` header and are not tracked at all (the ping sent when
  the user actually navigates to a preloaded page does the counting; forging
  the header only hides a GET from the crawler stats, the path-based abuse
  classification is unaffected).  If a ping
  from the same client arrives within 10 seconds the hit is discarded;
  otherwise it is written to `crawlers`.  Crawlers do not count as
  visits or views.  The `Accept-Language` header is stored on the shared
  `Client` immediately; reverse-DNS host names and DB-IP geoip
  country/city are filled in asynchronously, just like for real visits.  In
  the analytics viewer, crawler hits are grouped by client hash and shown as
  a trail of internal pages that crawler visited; the crawler table lists
  the most active crawlers first rather than the most recent hits.
- **Abuse (scanner) hits**: a 404 for a telltale path — any URL segment
  starting with a dot (`/.env`, `/.git/config`) or ending in `.php` —
  classifies the source IP as abuse immediately, and ten plain 404s from one
  IP do too.  Classification reclassifies history: all earlier crawler hits
  from that IP (persisted and pending) move to the `abuse` list, so a
  random-UA scanner no longer pollutes the crawler stats of the legitimate
  bot it impersonates.  Once classified, every document GET and 404 from the
  IP is recorded as an abuse hit with the full request path (query string
  included), and its pings are ignored.  The classified IP set (`abuse_ips`)
  is persisted in the JSON file; the plain-404 counters are RAM-only.  In the
  viewer, abuse hits are grouped by IP (never by client/UA — scanners
  randomize theirs) in a separate "Abuse" table.  Identical paths are
  collapsed into one entry with their hit count; flagged paths that
  triggered classification are lifted to the top, followed by other 404s and
  then document GETs from the abuser.  Raw User-Agent strings are shown one
  per line with their occurrence counts, and the full lists are click-to-copy.

## Visits and sessions

There are no cookies. A visit is tied together by a client hash — the first
6 bytes of a blake3 digest over the prettified IP (IPv4 unchanged, IPv6
/64 network), the raw `User-Agent` string and the extracted
`Accept-Language` tag.  The first ping from a client hash starts a new
visit; subsequent pings extend it.  Pings arriving with no known session
(server restart) start a fresh visit from the first ping — treated as
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
  parsable, otherwise the raw string.

Each `Visit` record:

- `start` — timestamp of the first event,
- `entry` — first page (path) seen,
- `referer` — external https origin of the initial load, `""` for direct,
- `client` — 6-byte blake3 hash referencing `Analytics.clients`,
- `trail` — everything seen afterwards in first-seen order: page paths and
  external exit URLs. Re-visiting an already seen page (incl. the entry)
  does not append.
- `utm` — `utm_*` query parameters from the landing URL, as a dict.
- `read` — active reading time per path (seconds), keyed by path.
- `statuses` — HTTP status of the response when each path was first seen
  (200 or 404), keyed by path.

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
- `is_404` — true for 404 responses, false for document GETs from the
  abuser.

Crawler hits are grouped by client hash in the analytics viewer; abuse hits
are grouped by IP alone (resolved from the referenced `Client`).  In the
Abuse table identical paths are collapsed with their counts; flagged paths
that triggered classification are lifted to the top, followed by other 404s
and then document GETs from the abuser.  Within each category paths are
sorted by count descending, then by their earliest hit.

In the visitor and crawler tables, internal paths that returned a 404 status
are shown in red and the link title includes the status code, so it is easy
to tell misses from real pages at a glance.

## Aggregates

- `transitions`: time series of page transitions, sparse nested dict
  `from -> to -> bucket -> count` with the same 5-minute bucketing as
  `views`. `from` is the referer origin or `"(direct)"` for initial loads,
  a page path for pings.
- `views`: time series of page loads, `path -> bucket -> count`, sparse: only
  non-zero 5-minute buckets exist (bucket key is its floored ISO timestamp).
  Every load counts, including repeats within a visit; external exit origins
  are not page views and are not counted here.
- `site_visits`: `bucket -> count` of new visits started, same sparse
  5-minute bucketing.

Sparseness keeps quiet sites small; dropping old data is a matter of deleting
list/dict entries (`visits` is a plain append-only list, buckets plain keys).

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
tapered connections whose middle width grows logarithmically with the
count (uncapped), connections
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
