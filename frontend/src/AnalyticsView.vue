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
import * as flagSvgs from 'country-flag-icons/string/3x2'
import TrailLink from './TrailLink.vue'
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
  url.searchParams.set('range', r)
  history.replaceState(null, '', url)
})

const visitRows = computed(() => formatVisitRows(visits.value, pageTree.value, now.value))
const crawlers = computed(() => data.value?.crawlers || [])
const crawlerRows = computed(() => formatCrawlerRows(crawlers.value, pageTree.value, now.value))
const abuseRows = computed(() => formatAbuseRows(data.value?.abuse || [], now.value))

function flagSvg(code) {
  return flagSvgs[code?.toUpperCase()] || ''
}

function countryName(code) {
  if (!code) return ''
  try {
    return new Intl.DisplayNames(['en'], { type: 'region' }).of(code.toUpperCase())
  } catch {
    return ''
  }
}
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
                    <span v-if="v.utm && v.utm !== '—'" class="utm-tag" :title="v.utmTitle">{{ v.utm }}</span>
                    <TrailLink v-for="(s, si) in v.trail" :key="si" :step="s" @close="$emit('close')" />
                  </td>
                  <td class="ip-locale-cell" :class="{ 'host-cell': v.isHost }">
                    <div class="ip-locale-rows">
                      <div class="ip-locale-row">
                        <div class="locale-line">
                          <span v-if="flagSvg(v.country)" class="flag" v-html="flagSvg(v.country)" :title="countryName(v.country) || v.country"></span>
                          <template v-if="v.city && v.city !== '—'"><small class="city-name">{{ v.city }}</small></template>
                          <template v-else-if="!flagSvg(v.country)">—</template>
                        </div>
                        <div class="ip-line"><span class="clickable-ip"
                                   :title="v.ip"
                                   @click="copyIp(v.ip, $event)">{{ v.ipDisplay }}</span></div>
                      </div>
                      <div class="ip-locale-row">
                        <div class="ua-line"><small class="muted" :title="v.uaRaw">{{ v.ua }}</small></div>
                        <div v-if="v.lang && v.lang !== '—'" class="locale-lang"><small class="muted">{{ v.langDisplay }}</small></div>
                      </div>
                    </div>
                  </td>
                  <td class="last-seen"
                      :title="v.lastSeenLocal"
                      @click="copyList(v.lastSeenIso, $event)">{{ v.lastSeen }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="empty">no visits recorded yet</p>
        </section>

        <section>
          <h2>Crawlers</h2>
          <div v-if="crawlerRows.length" class="visit-table-wrap">
            <table class="visit-table">
              <thead>
                <tr>
                  <th>pages</th>
                  <th>ip / ua</th>
                  <th class="last-seen">last seen</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(c, i) in crawlerRows" :key="i">
                  <td class="trail">
                    <TrailLink v-for="(s, si) in c.pages" :key="si" :step="s" :count="s.count" @close="$emit('close')" />
                  </td>
                  <td class="ip-ua-cell">
                    <div><span class="clickable-ip"
                               :title="c.ip"
                               @click="copyIp(c.ip, $event)">{{ c.ipDisplay }}</span></div>
                    <div class="ua-line"><small class="muted" :title="c.uaRaw">{{ c.ua }}</small></div>
                  </td>
                  <td class="last-seen"
                      :title="c.lastSeenLocal"
                      @click="copyList(c.lastSeenIso, $event)">{{ c.lastSeen }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="empty">no crawler hits recorded yet</p>
        </section>

        <section v-if="abuseRows.length">
          <h2>Abuse</h2>
          <div class="visit-table-wrap">
            <table class="visit-table">
              <thead>
                <tr>
                  <th>paths</th>
                  <th>ip / uas</th>
                  <th class="last-seen">last seen</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(a, i) in abuseRows" :key="i">
                  <td class="trail abuse-list clickable-list"
                      @click="copyList(a.allPaths, $event)">
                    <div v-for="(p, pi) in a.paths.slice(0, ABUSE_MAX_LINES)" :key="pi"
                         class="list-line">
                      {{ p.path }}
                    </div>
                    <div v-if="a.paths.length > ABUSE_MAX_LINES" class="list-line">
                      <small class="muted">+{{ a.paths.length - ABUSE_MAX_LINES }} more</small>
                    </div>
                  </td>
                  <td class="ip-ua-cell">
                    <div><span class="clickable-ip"
                               :title="a.ip"
                               @click="copyIp(a.ip, $event)">{{ a.ipDisplay }}</span></div>
                    <div class="abuse-uas-list clickable-list"
                         @click="copyList(a.allUas, $event)">
                      <div v-for="(u, ui) in a.uas.slice(0, ABUSE_MAX_LINES)" :key="ui"
                           class="list-line">
                        <small v-if="u.count > 1" class="muted">{{ formatCount(u.count) }}×</small>{{ u.ua }}
                      </div>
                      <div v-if="a.uas.length > ABUSE_MAX_LINES" class="list-line">
                        <small class="muted">+{{ a.uas.length - ABUSE_MAX_LINES }} more</small>
                      </div>
                    </div>
                  </td>
                  <td class="last-seen"
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
  width: 7.5rem;
  text-align: right;
  white-space: nowrap;
  color: var(--muted);
  cursor: pointer;
}

.visit-table .last-seen:hover {
  color: var(--accent);
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
  color: var(--text);
  text-decoration: none;
  vertical-align: bottom;
}

.visit-table .trail a:hover,
.visit-table .trail-link:hover { color: var(--accent); }

.visit-table .trail > * + * {
  margin-left: 0.5rem;
}

.visit-table .utm-tag {
  display: inline-block;
  max-width: 100%;
  padding: 0.05rem 0.4rem;
  border: 1px solid var(--line);
  border-radius: 0.25rem;
  font-size: 0.75em;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
}

.visit-table .clickable-list {
  cursor: pointer;
  max-width: 22rem;
}

.visit-table .clickable-list .list-line {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.35;
}

.visit-table .clickable-list .list-line + .list-line {
  margin-top: 0.15rem;
}

.visit-table .ua.abuse-uas {
  max-width: 24rem;
}

.visit-table .trail small,
.visit-table small.muted {
  color: var(--muted);
  font-size: 0.75em;
}

.visit-table .clickable-ip {
  font-size: 0.75em;
}

.visit-table .clickable-ip,
.visit-table .clickable-list,
.visit-table .last-seen {
  cursor: pointer;
  position: relative;
}

.visit-table .clickable-ip:hover,
.visit-table .clickable-list:hover,
.visit-table .last-seen:hover {
  color: var(--accent);
}

.visit-table .ip-locale-cell {
  width: 36ch;
  max-width: 36ch;
  overflow: hidden;
  text-overflow: ellipsis;
}

.visit-table .ip-ua-cell {
  width: 22ch;
  max-width: 22ch;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.visit-table .host-cell {
  text-align: right;
}

.visit-table .ip-locale-rows {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.visit-table .ip-locale-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.visit-table .ip-locale-row > * {
  min-width: 0;
}

.visit-table .ip-locale-row .locale-line,
.visit-table .ip-locale-row .ip-line,
.visit-table .ip-locale-row .ua-line {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.visit-table .ip-locale-row .locale-lang {
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: right;
}

.visit-table .ip-locale-row .locale-line {
  text-align: left;
}

.visit-table .ip-locale-row .ip-line {
  text-align: right;
}

.visit-table .ip-locale-row .ua-line {
  text-align: left;
}

.visit-table .locale-line {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.visit-table .city-name {
  display: inline-block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}

.visit-table .ua-line {
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.visit-table .abuse-uas-list {
  text-align: right;
}

.visit-table .copy-popup {
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

.visit-table .locale-line .flag {
  display: inline-flex;
  width: 18px;
  height: 12px;
  border-radius: 2px;
  overflow: hidden;
  border: 1px solid var(--line);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.2) inset;
  vertical-align: middle;
}

.visit-table .locale-line .flag :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}

.visit-table .locale-line .city-name {
  margin-left: 0.3rem;
  vertical-align: middle;
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
