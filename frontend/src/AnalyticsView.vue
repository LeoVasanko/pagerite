<script setup>
// Analytics viewer rendered as a normal page inside #main. Receives live
// analytics data over /_api/ws/analytics (admin-gated by the auth proxy) and
// renders totals, smoothed visit/views curves, a transition map, and recent
// visit/crawler tables. Read-only.
// See docs/analytics.md for the data format.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RANGES } from './analytics/time.js'
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

const props = defineProps({
  initialRange: { type: String, default: 'week' },
})

const ABUSE_MAX_LINES = 5

const data = ref(null)
const pageTree = ref(null)
const error = ref('')
const now = ref(Date.now())
let ws = null
let reconnectTimeout = null
let timeInterval = null

function connectAnalytics() {
  if (ws) return
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${proto}//${location.host}/_api/ws/analytics`)
  ws.onopen = () => { error.value = '' }
  ws.onmessage = (event) => {
    try {
      data.value = JSON.parse(event.data)
    } catch {
      error.value = 'analytics data could not be loaded'
    }
  }
  ws.onerror = () => {
    error.value = 'analytics data could not be loaded'
  }
  ws.onclose = () => {
    ws = null
    reconnectTimeout = setTimeout(connectAnalytics, 2000)
  }
}

onMounted(async () => {
  connectAnalytics()
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
  if (timeInterval) clearInterval(timeInterval)
  if (ws) {
    ws.onclose = null
    ws.close()
    ws = null
  }
})

const visits = computed(() => data.value?.visits || [])
const totalViews = computed(() => calcTotalViews(data.value?.views))
const readStats = computed(() => calcReadStats(visits.value))

const range = ref(RANGES[props.initialRange] ? props.initialRange : 'week')

// Keep the URL shareable when the range changes.
watch(range, (r) => {
  const url = new URL(location.href)
  url.hash = r
  history.replaceState(null, '', url)
})

const clients = computed(() => data.value?.clients || {})
const visitRows = computed(() => formatVisitRows(visits.value, clients.value, pageTree.value, now.value))
const crawlers = computed(() => data.value?.crawlers || [])
const crawlerRows = computed(() => formatCrawlerRows(crawlers.value, clients.value, pageTree.value, now.value))
const abuseRows = computed(() => formatAbuseRows(data.value?.abuse || [], clients.value, now.value))

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
      <p v-if="error" class="error">⚠️ {{ error }}</p>
      <p v-else-if="!data" class="loading">loading…</p>
      <template v-else>
        <section class="totals">
          <div><strong :title="String(visits.length)">{{ formatCount(visits.length) }}</strong> visits</div>
          <div><strong :title="String(totalViews)">{{ formatCount(totalViews) }}</strong> page views</div>
          <div><strong>{{ readStats.avgMinPerVisit }}</strong> min/visit</div>
          <div><strong>{{ readStats.avgArticleMedianMin }}</strong> min article read</div>
        </section>

        <VisitorCharts :data="data" :range="range" />
        <TransitionGraph :data="data" :range="range" :page-tree="pageTree" />

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
                    <TrailLink v-if="v.refererStep" :step="v.refererStep" @close="$emit('close')" />
                    <span v-if="v.utm && v.utm !== '—'" class="utm-tag small muted" :title="v.utmTitle">{{ v.utm }}</span>
                    <TrailLink v-for="(s, si) in v.trail" :key="si" :step="s" @close="$emit('close')" />
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
          <p v-else class="empty">no crawler hits recorded yet</p>

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
  margin: 0 auto;
  width: min(60rem, 96vw);
  padding: 1.5rem 2rem 4rem;
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
  font-size: 0.85rem;
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

.totals {
  display: flex;
  gap: 2rem;
  font-size: 1.1rem;
}
.totals strong { font-size: 1.5rem; }

.visit-table-wrap {
  overflow-x: auto;
}

.visit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
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
  width: 5rem;
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
