<script setup>
// Analytics viewer rendered as a normal page inside #main. Fetches the raw
// collected data from /_api/analytics (admin-gated by the auth proxy) and
// renders totals, smoothed visit/views curves, a transition map, and recent
// visit/crawler tables. Read-only.
// See docs/analytics.md for the data format.
import { computed, onMounted, ref, watch } from 'vue'
import { RANGES } from './analytics/time.js'
import {
  calcTotalViews,
  copyIp,
  countCrawlerUas,
  formatCounts,
  formatCrawlerRows,
  formatVisitRows,
} from './analytics/format.js'
import * as flagSvgs from 'country-flag-icons/string/3x2'
import TransitionGraph from './TransitionGraph.vue'
import VisitorCharts from './VisitorCharts.vue'

const props = defineProps({
  initialRange: { type: String, default: 'week' },
})

const data = ref(null)
const pageTree = ref(null)
const error = ref('')

onMounted(async () => {
  try {
    const res = await fetch('/_api/analytics')
    if (!res.ok) throw new Error(res.statusText)
    data.value = await res.json()
  } catch {
    error.value = 'analytics data could not be loaded'
  }
  // The site tree for the transition map (all pages in menu order). Not
  // fatal: without it the map falls back to transition endpoints only.
  try {
    const res = await fetch('/_api/pages')
    if (res.ok) pageTree.value = await res.json()
  } catch { /* map just narrows to pages seen in transitions */ }
})

const visits = computed(() => data.value?.visits || [])
const totalViews = computed(() => calcTotalViews(data.value?.views))

const range = ref(RANGES[props.initialRange] ? props.initialRange : 'week')

// Keep the URL shareable when the range changes.
watch(range, (r) => {
  const url = new URL(location.href)
  url.searchParams.set('range', r)
  history.replaceState(null, '', url)
})

const visitRows = computed(() => formatVisitRows(visits.value, pageTree.value))
const crawlers = computed(() => data.value?.crawlers || [])
const crawlerRows = computed(() => formatCrawlerRows(crawlers.value))
const topCrawlerUas = computed(() => countCrawlerUas(crawlers.value).slice(0, 10))

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
          <div><strong>{{ visits.length }}</strong> visits</div>
          <div><strong>{{ totalViews }}</strong> page views</div>
        </section>

        <VisitorCharts :data="data" :range="range" />
        <TransitionGraph :data="data" :range="range" :page-tree="pageTree" />

        <section>
          <h2>Recent visits</h2>
          <div v-if="visitRows.length" class="visit-table-wrap">
            <table class="visit-table">
              <thead>
                <tr>
                  <th>when</th>
                  <th>trail</th>
                  <th>referer</th>
                  <th>ip</th>
                  <th>lang</th>
                  <th>country</th>
                  <th>ua</th>
                  <th>utm</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(v, i) in visitRows" :key="i">
                  <td class="when">{{ v.when }}</td>
                  <td class="trail">
                    <a v-for="(s, si) in v.trail" :key="si"
                       :href="s.path" :title="s.title" @click="emit('close')">
                      {{ s.slug }}
                    </a>
                  </td>
                  <td>{{ v.referer }}</td>
                  <td>
                    <span class="clickable-ip"
                          :title="`Click to copy full IP: ${v.ip}`"
                          @click="copyIp(v.ip)">{{ v.ipDisplay }}</span>
                  </td>
                  <td>{{ v.lang }}</td>
                  <td class="country">
                    <span v-if="flagSvg(v.country)" class="flag" v-html="flagSvg(v.country)" :title="countryName(v.country) || v.country"></span>
                    <template v-else>—</template>
                  </td>
                  <td class="ua" :title="v.uaRaw">{{ v.ua }}</td>
                  <td>{{ v.utm }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="empty">no visits recorded yet</p>
        </section>

        <section>
          <h2>Crawlers</h2>
          <div v-if="topCrawlerUas.length" class="crawler-top-uas">
            <p><strong>top UAs:</strong> {{ formatCounts(topCrawlerUas) }}</p>
          </div>
          <div v-if="crawlerRows.length" class="visit-table-wrap">
            <table class="visit-table">
              <thead>
                <tr>
                  <th>when</th>
                  <th>entry</th>
                  <th>ip</th>
                  <th>ua</th>
                  <th>referer</th>
                  <th>query</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(c, i) in crawlerRows" :key="i">
                  <td class="when">{{ c.when }}</td>
                  <td>{{ c.entry }}</td>
                  <td>
                    <span class="clickable-ip"
                          :title="`Click to copy full IP: ${c.ip}`"
                          @click="copyIp(c.ip)">{{ c.ipDisplay }}</span>
                  </td>
                  <td class="ua" :title="c.uaRaw">{{ c.ua }}</td>
                  <td>{{ c.referer }}</td>
                  <td>{{ c.query }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="empty">no crawler hits recorded yet</p>
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
  font-family: monospace;
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

.visit-table .when {
  white-space: nowrap;
  color: var(--muted);
}

.visit-table .trail {
  max-width: 20rem;
  overflow-wrap: break-word;
}

.visit-table .trail a {
  color: var(--text);
  text-decoration: none;
}

.visit-table .trail a:hover { color: var(--accent); }

.visit-table .trail a + a {
  margin-left: 0.5rem;
}

.visit-table .clickable-ip {
  cursor: pointer;
  text-decoration: underline;
  text-decoration-style: dotted;
}

.visit-table .clickable-ip:hover {
  color: var(--accent);
}

.visit-table .ua {
  max-width: 18rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.visit-table .country .flag {
  display: inline-flex;
  width: 18px;
  height: 12px;
  border-radius: 2px;
  overflow: hidden;
  border: 1px solid var(--line);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.2) inset;
}

.visit-table .country .flag :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
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
