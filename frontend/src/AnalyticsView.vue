<script setup>
// Analytics viewer rendered as a normal page inside #main. Receives live
// analytics data over /_api/ws/analytics (admin-gated by the auth proxy) and
// renders totals, smoothed visit/views curves, a transition map, and recent
// visit/crawler tables. Read-only.
// See docs/analytics.md for the data format.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  RANGES,
  rangeWindow,
  filterRecordsByRange,
  filterTransitionsByRange,
  filterViewsByRange,
} from './analytics/time.js'
import {
  calcReadStats,
  calcTotalViews,
  copyIp,
  copyList,
  formatCount,
  formatAbuseRows,
  formatCrawlerRows,
  formatVisitRows,
} from './analytics/format.js'
import TrailLink from './TrailLink.vue'
import VisitorCell from './VisitorCell.vue'
import TransitionGraph from './TransitionGraph.vue'
import VisitorCharts from './VisitorCharts.vue'
import { VIEW_W } from './analytics/chart.js'
import { reconnectPolicy, socketSlot, watchConnecting } from './reconnect'
import ConnNote from './ConnNote.vue'

// Same centering margin as the charts, so the totals row's left edge
// aligns with the chart svg above the natural width.
const CHART_MARGIN = `max(0px, calc(50% - ${VIEW_W / 2}px))`

const ABUSE_MAX_LINES = 5

const data = ref(null)
const pageTree = ref(null)
const error = ref('')
const now = ref(Date.now())
let ws = null
let reconnectTimeout = null
let connectWatchdog = null
const reconnects = reconnectPolicy()
let timeInterval = null

// The panel is live data over its socket: while it is connecting or waiting
// to reconnect, say so (ConnNote) instead of showing a silent stale view.
const conn = ref('connecting') // connecting | open | waiting
const retryIn = ref(0)
const connNote = computed(() =>
  conn.value === 'connecting' ? 'connecting to the server…'
    : conn.value === 'waiting' ? `connection lost — reconnecting in ~${retryIn.value} s…`
    : '',
)

// The initial range comes from the URL hash (shareable links); without one,
// it is derived from the first analytics snapshot: day when the recorded
// history is shorter than 24 h, week otherwise.
const hashRange = location.hash.slice(1)
const range = ref(RANGES[hashRange] ? hashRange : 'week')
let rangePinned = Boolean(RANGES[hashRange])

function connectAnalytics() {
  if (ws) return
  conn.value = 'connecting'
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${proto}//${location.host}/_api/ws/analytics`)
  clearTimeout(connectWatchdog)
  connectWatchdog = watchConnecting(ws, 'analytics')
  ws.onopen = () => {
    conn.value = 'open'
    reconnects.opened()
    error.value = ''
  }
  ws.onmessage = (event) => {
    try {
      data.value = JSON.parse(event.data)
      if (!rangePinned) {
        rangePinned = true
        const starts = (data.value?.visits || [])
          .map((v) => Date.parse(v.start))
          .filter((t) => !Number.isNaN(t))
        if (starts.length && Date.now() - Math.min(...starts) < 24 * 3600 * 1000) {
          range.value = 'day'
        }
      }
    } catch {
      error.value = 'analytics data could not be loaded'
    }
  }
  ws.onerror = () => {
    error.value = 'analytics data could not be loaded'
  }
  ws.onclose = () => {
    ws = null
    // The policy paces the retry: doubling backoff with jitter, reset only
    // by a healthy connection — a fixed rapid loop trips the browser's
    // WebSocket throttling (all sockets then sit "pending" for minutes).
    const wait = reconnects.closed()
    retryIn.value = Math.max(1, Math.round(wait / 1000))
    conn.value = 'waiting'
    reconnectTimeout = setTimeout(connectAnalytics, wait)
  }
}

onMounted(async () => {
  // The first connection takes a staggered slot (see ./reconnect).
  reconnectTimeout = setTimeout(connectAnalytics, socketSlot())
  now.value = Date.now()
  timeInterval = setInterval(() => { now.value = Date.now() }, 1000)
  // The site tree for the transition map (all pages in menu order). Not
  // fatal: without it the map just narrows to pages seen in transitions.
  try {
    const res = await fetch('/_api/pages')
    if (res.ok) pageTree.value = await res.json()
  } catch { /* map just narrows to pages seen in transitions */ }
})

onUnmounted(() => {
  if (reconnectTimeout) clearTimeout(reconnectTimeout)
  if (connectWatchdog) clearTimeout(connectWatchdog)
  if (timeInterval) clearInterval(timeInterval)
  if (ws) {
    ws.onclose = null
    ws.close()
    ws = null
  }
})

const window = computed(() => rangeWindow(range.value))

// All non-chart stats follow the selected range; the charts keep their own
// range-specific x windows (week overlays previous weeks aligned to Monday).
const rangeData = computed(() => {
  if (!data.value) return null
  const { t0, t1 } = window.value
  return {
    ...data.value,
    transitions: filterTransitionsByRange(data.value.transitions, t0, t1),
    views: filterViewsByRange(data.value.views, t0, t1),
    visits: filterRecordsByRange(data.value.visits, t0, t1),
    crawlers: filterRecordsByRange(data.value.crawlers, t0, t1),
    abuse: filterRecordsByRange(data.value.abuse, t0, t1),
  }
})

const visits = computed(() => rangeData.value?.visits || [])
const totalViews = computed(() => calcTotalViews(rangeData.value?.views))
const readStats = computed(() => calcReadStats(visits.value))

// Keep the URL shareable when the range changes.
watch(range, (r) => {
  const url = new URL(location.href)
  url.hash = r
  history.replaceState(history.state, '', url)
})

const clients = computed(() => data.value?.clients || {})
const favicons = computed(() => data.value?.favicons || {})
const visitRows = computed(() => formatVisitRows(visits.value, clients.value, pageTree.value, now.value))
const crawlers = computed(() => rangeData.value?.crawlers || [])
const crawlerRows = computed(() => formatCrawlerRows(crawlers.value, clients.value, pageTree.value, now.value))
const abuseRows = computed(() => formatAbuseRows(rangeData.value?.abuse || [], clients.value, now.value))

</script>

<template>
  <div class="analytics-view">
    <div class="analytics-panel">
      <header>
        <h1>Analytics</h1>
        <nav class="ranges">
          <button v-for="(r, key) in RANGES" :key="key" type="button"
                  :class="{ active: range === key }" @click="range = key">
            {{ r.label }}
          </button>
        </nav>
        <a href="/" class="close" title="home">✕</a>
      </header>
      <ConnNote :text="connNote" />
      <p v-if="error" class="error">⚠️ {{ error }}</p>
      <p v-else-if="!data" class="loading">loading…</p>
      <template v-else>
        <section class="totals" :style="{ marginLeft: CHART_MARGIN }">
          <div><strong :title="String(visits.length)">{{ formatCount(visits.length) }}</strong> visits</div>
          <div><strong :title="String(totalViews)">{{ formatCount(totalViews) }}</strong> page views</div>
          <div><strong>{{ readStats.avgMinPerVisit }}</strong> min/visit</div>
          <div><strong>{{ readStats.avgArticleMedianMin }}</strong> min/read</div>
        </section>

        <VisitorCharts :data="data" :range="range" />
        <TransitionGraph :data="rangeData" :window="window" :page-tree="pageTree" :favicons="favicons" />

        <section>
          <h2>Recent visits</h2>
          <div v-if="visitRows.length" class="visit-table-wrap">
            <table class="visit-table">
              <thead>
                <tr>
                  <th>trail</th>
                  <th>visitor</th>
                  <th class="last-seen">last seen</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(v, i) in visitRows" :key="i">
                  <td class="trail">
                    <TrailLink v-if="v.refererStep" :step="v.refererStep" :favicons="favicons" @close="$emit('close')" />
                    <span v-if="v.utm && v.utm !== '—'" class="utm-tag small muted" :title="v.utmTitle">{{ v.utm }}</span>
                    <TrailLink v-for="(s, si) in v.trail" :key="si" :step="s" :favicons="favicons" @close="$emit('close')" />
                  </td>
                  <VisitorCell
                    :ip="v.ip"
                    :ip-display="v.ipDisplay"
                    :ua="v.ua"
                    :ua-raw="v.uaRaw"
                    :country="v.country"
                    :city="v.city"
                    :lang="v.lang"
                    :lang-display="v.langDisplay"
                    :is-host="v.isHost"
                  />
                  <td class="last-seen muted"
                      :title="v.lastSeenLocal"
                      @click="copyList(v.lastSeenIso, $event)">{{ v.lastSeen }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="empty">no visits recorded yet</p>

          <div v-if="crawlerRows.length" class="visit-table-wrap">
            <table class="visit-table">
              <thead>
                <tr>
                  <th>pages crawled</th>
                  <th>visitor</th>
                  <th class="last-seen">last seen</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(c, i) in crawlerRows" :key="i">
                  <td class="trail">
                    <TrailLink v-for="(s, si) in c.pages" :key="si" :step="s" :count="s.count" @close="$emit('close')" />
                  </td>
                  <VisitorCell
                    :ip="c.ip"
                    :ip-display="c.ipDisplay"
                    :ua="c.ua"
                    :ua-raw="c.uaRaw"
                    :country="c.country"
                    :city="c.city"
                    :lang="c.lang"
                    :lang-display="c.langDisplay"
                    :is-host="c.isHost"
                  />
                  <td class="last-seen muted"
                      :title="c.lastSeenLocal"
                      @click="copyList(c.lastSeenIso, $event)">{{ c.lastSeen }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="abuseRows.length" class="visit-table-wrap">
            <table class="visit-table">
              <thead>
                <tr>
                  <th>paths abused</th>
                  <th>visitor</th>
                  <th class="last-seen">last seen</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(a, i) in abuseRows" :key="i">
                  <td class="trail abuse-list clickable-list"
                      @click="copyList(a.allPaths, $event)">
                    <div class="abuse-items">
                      <span v-for="(p, pi) in a.paths.slice(0, ABUSE_MAX_LINES)" :key="pi"
                            class="inline-item">
                        <small v-if="p.count > 1" class="muted">{{ formatCount(p.count) }}×</small>{{ p.path }}
                      </span>
                      <small v-if="a.paths.length > ABUSE_MAX_LINES" class="muted">+{{ a.paths.length - ABUSE_MAX_LINES }} more</small>
                    </div>
                  </td>
                  <VisitorCell
                    :ip="a.ip"
                    :ip-display="a.ipDisplay"
                    :ua="a.ua"
                    :ua-raw="a.uaRaw"
                    :country="a.country"
                    :city="a.city"
                    :lang="a.lang"
                    :lang-display="a.langDisplay"
                    :is-host="a.isHost"
                    :variant-count="a.clientCount"
                  />
                  <td class="last-seen muted"
                      :title="a.lastSeenLocal"
                      @click="copyList(a.lastSeenIso, $event)">{{ a.lastSeen }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.analytics-view {
  min-height: 100vh;
  background: var(--bg, Canvas);
  color: var(--text, CanvasText);
}

.analytics-panel {
  margin: 0;
  width: 100%;
  /* Same 1.25rem side spacing as main's article padding. */
  padding: 1.5rem 1.25rem 4rem;
  /* Container for cqw-based shrink-to-fit (see .totals). */
  container-type: inline-size;
}

.analytics-panel header {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.analytics-panel h1 {
  margin: 0;
  font-size: 1.4rem;
}

.ranges {
  display: flex;
  gap: 0.25rem;
  margin-left: auto;
}

.ranges button {
  padding: 0.2rem 0.7rem;
  font: inherit;
  font-size: 0.9rem;
  color: var(--muted);
  background: none;
  border: 1px solid var(--line);
  border-radius: 1rem;
  cursor: pointer;
}

.ranges button:hover { color: var(--text); }

.ranges button.active {
  color: var(--text);
  border-color: var(--accent);
}

.close {
  padding: 0 0.3rem;
  background: none;
  border: none;
  color: var(--muted);
  font-size: 1.2rem;
  cursor: pointer;
}
.close:hover { color: var(--text); }

.analytics-panel h2 {
  margin: 0 0 0.6rem;
  font-size: 1rem;
  color: var(--muted);
}

.analytics-panel section {
  margin-top: 1.8rem;
}

.analytics-view a {
  color: var(--text);
  text-decoration: none;
}
.analytics-view a:hover { color: var(--accent); }

.analytics-view :deep(.muted) { color: var(--muted); }
.analytics-view :deep(.small) { font-size: 0.75em; }

/* One line at any width: the gap shrinks first, then the font (the number
   scales along in em), both following the panel's container width. */
.totals {
  display: flex;
  gap: clamp(0.5rem, 3cqw, 2rem);
  font-size: clamp(0.6rem, 2.2cqw, 1.1rem);
  white-space: nowrap;
}
.totals strong { font-size: 1.36em; }

.visit-table-wrap {
  overflow-x: auto;
}

.visit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
  line-height: 1.3;
}

.visit-table th,
.visit-table td {
  padding: 0.25rem 0.5rem;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}

.visit-table th {
  color: var(--muted);
  font-weight: normal;
  text-transform: lowercase;
  position: sticky;
  top: 0;
  background: var(--bg, Canvas);
}

.visit-table .last-seen {
  width: 6rem;
  text-align: right;
  white-space: nowrap;
  cursor: pointer;
}

.visit-table .trail {
  max-width: 20rem;
  overflow-wrap: break-word;
}

.visit-table .trail a,
.visit-table .trail-link {
  display: inline-block;
  max-width: 8rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
}

.visit-table .trail > * + * {
  margin-left: 0.5rem;
}

.analytics-view :deep(.trail-link.error),
.analytics-view :deep(.trail-link.error:hover) {
  color: var(--error, #c00);
}

.visit-table .utm-tag {
  display: inline-block;
  max-width: 100%;
  padding: 0.05rem 0.4rem;
  border: 1px solid var(--line);
  border-radius: 0.25rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
}

.visit-table .clickable-list {
  cursor: pointer;
  max-width: 22rem;
}

.visit-table .abuse-items {
  display: flex;
  flex-wrap: wrap;
  gap: 0.15rem 0.5rem;
  align-items: baseline;
}

.visit-table .inline-item {
  max-width: 18rem;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: keep-all;
  hyphens: none;
}

.visit-table :deep(.clickable-ip),
.visit-table .clickable-list,
.visit-table .last-seen {
  cursor: pointer;
  position: relative;
}

.visit-table :deep(.copy-popup) {
  position: absolute;
  bottom: calc(100% + 0.25rem);
  left: 50%;
  transform: translateX(-50%);
  padding: 0.15rem 0.4rem;
  background: var(--text, CanvasText);
  color: var(--bg, Canvas);
  border-radius: 0.25rem;
  font-size: 0.75rem;
  white-space: nowrap;
  pointer-events: none;
  z-index: 10;
}

.crawler-top-uas {
  font-size: 0.9rem;
  margin-bottom: 0.6rem;
}

.crawler-top-uas strong {
  color: var(--muted);
}

.empty, .loading, .error { color: var(--muted); }
.error { color: var(--error, #c00); }
</style>
