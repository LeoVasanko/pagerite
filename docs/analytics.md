# Analytics

Server-side visit analytics. Data lives in a plain JSON file — a msgspec
Struct dumped to disk — separate from the kanta content database, path from
`PAGERITE_ANALYTICS` (default: the database path with `.kantadb` replaced by
`.analytics.json`, e.g. `pagerite.analytics.json`).

- `pagerite/analytics.py` — data model (`Analytics`, `Visit`) and the `Store`
  (in-memory data + session map, atomic JSON persistence).
- `pagerite/app.py` — entry-referer stashing in `show_page` (`_track_entry`),
  the `POST /_a` ping endpoint, and `GET /_api/analytics` (admin-gated like
  every `/_api` endpoint).
- `frontend/src/pagerite.js` — client navigation pings and the 📊 pen.
- `frontend/src/AnalyticsView.vue` — full-screen viewer (its own Vue app via
  `openAnalytics()`/`closeAnalytics()` in `main.js`, not a docked-panel tab).

## What is collected

The client (`pagerite.js`) POSTs fire-and-forget pings to `/_a` with
`{fr, to}` (`fr` = source path):

- **Initial page load**: `to` is the loaded path. This ping is what starts
  the visit and counts the entry page view — the document GET alone records
  nothing, so bots and admin browsing never register. Reloads are not
  visits: the ping is skipped (PerformanceNavigationTiming `reload`), so a
  refresh neither counts a second view nor logs a self-transition. The GET
  handler stashes a cross-origin https `Referer` (origin part only) and any
  `utm_*` query parameters in in-memory IP tables, consumed by the ping that
  starts the visit; internal or absent referers never touch the referer table.
- **Internal fetch-navigations**: `to` is the target path, sent only after
  the swap actually happened (a failed swap falls back to a full load,
  whose initial ping counts the view instead — no gap, no double count).
- **External links** (`https` only): `to` is the link's origin. This is the
  exit-link record; the user may continue navigating afterwards (new tab,
  back), so the exit origin is not necessarily the last trail entry.
- **Excluded**: back/forward (popstate) navigations, and everything while
  the user is known to be an admin *and SSO is actually in use* — with no
  auth proxy (dev/test) "admin" is everyone's state, so the gate is off and
  everything is recorded — or has the editor open (`body.editing`) or the
  analytics view open (`body.analytics-open`) — admin noise, not visits.
- The server validates `to`: internal paths must be valid slug paths
  ("/" or `[a-z0-9_-]` segments), external ones are re-derived to the
  https origin and accepted only when the client sent exactly that.
- The initial ping also records the visitor's `User-Agent` and
  `Accept-Language` headers. The first `Accept-Language` tag is stored as
  `lang` (e.g. `en-us`) and its region subtag, if present, is stored as
  an initial `country` (e.g. `US`).
- The visitor IP is stored.  A reverse-DNS lookup is attempted for each new
  visit and the result, when available, is cached in RAM and stored as
  `host`; local/reserved/multicast addresses are skipped.
- If a DB-IP MMDB file (`dbip-*.mmdb` or `dbip-*.mmdb.gz`) is present in the
  repository root, it is loaded at startup and used to look up a more accurate
  `country`.  The MMDB lookup and the reverse-DNS lookup run in background
  tasks after the visit is stored, so the `/ _a` response is never delayed.
  The decompressed `dbip-*.mmdb` file is kept in the repository root and
  ignored by git.
- **Crawler hits**: every document GET is queued in RAM as a pending crawler
  hit.  If a ping from the same (IP, User-Agent) pair arrives within 10
  seconds the hit is discarded; otherwise it is written to `crawlers`.
  Crawlers do not count as visits or views.

## Visits and sessions

There are no cookies. A visit is tied together by the (IP, User-Agent) pair
(IP from the first `X-Forwarded-For` hop — we sit behind a proxy — else the
direct peer): the first ping from a pair starts a new visit, subsequent
pings extend it. Pings arriving with no known session (server restart)
start a fresh visit from the first ping — treated as missing data rather
than dropped. The (IP, UA) → visit map and the IP → entry-referer/UTM
tables are in-memory only, but the IP and any resolvable reverse-DNS host
name are stored on the `Visit` record itself.

Each `Visit` record:

- `start` — timestamp of the first event,
- `entry` — first page (path) seen,
- `referer` — external https origin of the initial load, `""` for direct,
- `ip` — visitor IP address (first `X-Forwarded-For` hop, or direct peer),
- `host` — reverse-DNS host name for `ip` when resolvable, else `""`,
- `trail` — everything seen afterwards in first-seen order: page paths and
  external exit origins. Re-visiting an already seen page (incl. the entry)
  does not append.
- `lang` — first `Accept-Language` tag, lowercased (e.g. `en-us`),
- `country` — two-letter country code.  Initially derived from the
  `Accept-Language` region subtag, but overwritten by the DB-IP MMDB result
  when a database is available,
- `ua` — raw `User-Agent` string from the initial ping,
- `ua_pretty` — compact display form of the UA (browser/OS/device) when
  parsable, otherwise the raw string,
- `utm` — `utm_*` query parameters from the landing URL, as a dict.

Each `CrawlerHit` record:

- `start` — timestamp of the document GET,
- `entry` — page path requested,
- `ip` — IP address,
- `ua` — raw `User-Agent` header,
- `ua_pretty` — compact display form of the UA when parsable,
- `referer` — external https origin of the request, `""` for direct/none,
- `query` — raw query string of the request.

Crawler hits are grouped by User-Agent in the analytics viewer.

## Aggregates

- `transitions`: sparse nested dict `from -> to -> count`. `from` is the
  referer origin or `"(direct)"` for initial loads, a page path for pings.
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
the edit pens) opens `AnalyticsView.vue` — a true full-screen app, not an
overlay: `body.analytics-open` hides the page chrome and the document itself
scrolls the view, styled by the active theme's variables. It is addressable
by URL: `#/analytics/<range>` (`week` default; opening via the pen pushes a
history entry so the back button exits, and pagerite.js auto-opens it on
load for editors when the hash is present, so refresh and link sharing work).

Charts are SVG curves (Catmull-Rom over an edge-aware adaptive Gaussian —
a change-point detector splits the series at traffic-level shifts, then
each segment is smoothed with a bandwidth that ramps with a broad pilot
estimate of the local rate: isolated events stay narrow (~0.4-unit sigma,
peaking at ~1 event/unit), busy traffic widens to a 1-unit sigma. The raw
series is drawn faint underneath). Values are
**per-unit rates** — per hour on the week view (5-minute bucket counts × 12,
plotted at native 5-minute resolution), per day on the month+ ranges — and
the smoothing time scale follows the unit: the month+ sigmas are 24× the
hourly ones. The y max is derived from the smoothed curves so single-bucket
spikes don't blow up the scale, and raw spikes are clamped into the plot.
Axes always start at 0 and end at a multiple of a 1-2-5 major step (max 5
labeled intervals, minor lines at fifths when integral; the floor is 1/h).
The week range is aligned to Monday 00:00 UTC and overlays up to 8 previous
weeks in the same accent color at decreasing opacity (the current week is
truncated at the current bucket, never drawing fake zeroes for the future);
its x labels are weekday names centered at midday UTC, without vertical grid
lines (day boundaries would be misleading in the viewer's timezone). The
month view labels days the same lineless way — day numbers at noon UTC,
with the month name substituted for the 1st. Year and all are rolling
windows ending at now, re-bucketed to daily points, with boundary lines at
months/years. Below the charts: a radial **transition map** (all pages from
`/_api/pages` — front page at the center, each slug level on its own ring,
siblings clockwise in navigation order from the top, radial gap equal to
the arc spacing — opposite transition directions joined into organic
tapered connections whose middle width is the total count over the full
recorded timescale; internal navigation only for now), per-page view
counts, the top transitions and the 50 most recent visit trails. Data comes from `GET /_api/analytics`, which
returns the raw JSON file contents.
