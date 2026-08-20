<script setup>
// Full-screen analytics app (replaces the page chrome while open; opened via
// the 📊 pen or directly by URL hash #/analytics/<range>, so refresh and link
// sharing work). Fetches the raw collected data from /_api/analytics
// (admin-gated by the auth proxy) and renders it: totals, smoothed
// visit/views curves over a selectable range, a transition map, and the
// recent visit trails. Read-only.
// See docs/analytics.md for the data format.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RANGES } from './analytics/time.js'
import { calcTotalViews, formatRecentVisits } from './analytics/format.js'
import TransitionGraph from './TransitionGraph.vue'
import VisitorCharts from './VisitorCharts.vue'

const props = defineProps({
  initialRange: { type: String, default: 'week' },
})
const emit = defineEmits(['close'])

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

function onKeydown(ev) {
  if (ev.key === 'Escape') emit('close')
}
onMounted(() => addEventListener('keydown', onKeydown))
onUnmounted(() => removeEventListener('keydown', onKeydown))

const visits = computed(() => data.value?.visits || [])
const totalViews = computed(() => calcTotalViews(data.value?.views))

const range = ref(RANGES[props.initialRange] ? props.initialRange : 'week')

// Keep the URL shareable: the hash names the open view and its range.
watch(range, (r) => {
  if (location.hash.startsWith('#/analytics')) {
    history.replaceState(null, '', `#/analytics/${r}`)
  }
})

const recentVisits = computed(() => formatRecentVisits(visits.value, pageTree.value))
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
        <button type="button" class="close" title="close" @click="emit('close')">✕</button>
      </header>
      <p v-if="error" class="error">⚠️ {{ error }}</p>
      <p v-else-if="!data" class="loading">loading…</p>
      <template v-else>
        <section class="totals">
          <div><strong>{{ visits.length }}</strong> visits</div>
          <div><strong>{{ totalViews }}</strong> page views</div>
        </section>

        <VisitorCharts :data="data" :range="range" />
        <TransitionGraph :data="data" :range="range" :page-tree="pageTree" @close="emit('close')" />

        <section>
          <h2>Recent visits</h2>
          <ul v-if="recentVisits.length" class="visits">
            <li v-for="(v, i) in recentVisits" :key="i">
              <span class="when">{{ v.when }}</span>
              <span class="trail">
                <a v-for="(s, si) in v.steps" :key="si"
                   :href="s.path" :title="s.title" @click="emit('close')">
                  {{ s.slug }}
                </a>
              </span>
            </li>
          </ul>
          <p v-else class="empty">no visits recorded yet</p>
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

.visits {
  list-style: none;
  margin: 0;
  padding: 0;
}
.visits li {
  display: flex;
  gap: 1rem;
  padding: 0.2rem 0;
  border-bottom: 1px solid var(--line);
}
.visits .when {
  flex-shrink: 0;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.visits .trail {
  font-family: monospace;
  word-break: normal;
  overflow-wrap: break-word;
}
.visits .trail a {
  color: var(--text);
  text-decoration: none;
}
.visits .trail a:hover { color: var(--accent); }
.visits .trail a + a {
  margin-left: 0.5rem;
}

.empty, .loading, .error { color: var(--muted); }
.error { color: var(--error, #c00); }
</style>

<style>
/* True full screen: while the analytics app is open the page chrome is
   hidden, so the document itself (not an overlay) scrolls the view. */
body.analytics-open #banner,
body.analytics-open #content,
body.analytics-open > footer {
  display: none;
}
</style>
