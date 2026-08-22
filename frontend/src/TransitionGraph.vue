<script setup>
/**
 * Radial transition map filtered to the selected time range.
 *
 * Transitions are stored per 5-minute bucket (from -> to -> bucket ->
 * count), so the graph sums the buckets falling inside the selected
 * range, exactly like the charts and per-page views do.
 */
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { rangeWindow, WEEK } from './analytics/time.js'
import { formatCount } from './analytics/format.js'
import {
  TNODE_W,
  TNODE_H,
  BEAD_R,
  BEAD_SPEED,
  buildTransitionGraph,
  filterTransitionsByRange,
  filterViewsByRange,
  filterVisitsByRange,
} from './analytics/transitions.js'

const props = defineProps({
  data: { type: Object, default: null },
  range: { type: String, required: true },
  pageTree: { type: Array, default: null },
})

const window = computed(() => rangeWindow(props.range))

const visualScale = computed(() => {
  const { t0, t1 } = window.value
  if (t0 != null && t1 != null) return WEEK / (t1 - t0)
  // 'all': scale by the actual data span.
  const times = new Set()
  for (const buckets of Object.values(props.data?.views || {})) {
    for (const k of Object.keys(buckets)) times.add(Date.parse(k))
  }
  const arr = [...times]
  if (arr.length < 2) return 1
  return WEEK / (Math.max(...arr) - Math.min(...arr))
})

const filteredData = computed(() => {
  if (!props.data) return null
  const { t0, t1 } = window.value
  return {
    transitions: filterTransitionsByRange(props.data.transitions, t0, t1),
    views: filterViewsByRange(props.data.views, t0, t1),
  }
})

const filteredVisits = computed(() =>
  filterVisitsByRange(props.data?.visits, window.value.t0, window.value.t1),
)

const graph = computed(() =>
  filteredData.value
    ? buildTransitionGraph(filteredData.value, props.pageTree, filteredVisits.value, visualScale.value)
    : null,
)

// Bead animation: every bead is simulated independently in JS. Each flow
// (one per edge direction) emits a bead every `interval` seconds; beads
// travel at BEAD_SPEED along the segment and are dropped at the end.
// There is deliberately no cap on beads in flight.
const beads = shallowRef([])
let rafId = 0

const MAX_BEAD_RATE = 120 // upper bound on total beads per second

const startBeads = (flows) => {
  cancelAnimationFrame(rafId)
  beads.value = []
  if (!flows?.length) return
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return

  // Cap the total bead emission rate so a busy range cannot spawn enough
  // beads to kill the page. Existing per-range time scaling is preserved;
  // this is only a proportional emergency throttle when the limit is hit.
  const totalRate = flows.reduce((s, f) => s + 1 / f.interval, 0)
  const scale = totalRate > MAX_BEAD_RATE ? MAX_BEAD_RATE / totalRate : 1

  const live = [] // { flow, t0 } — one entry per bead in flight
  const now = performance.now()
  const emitters = flows.map((flow) => {
    const interval = (flow.interval / scale) * 1000
    // Pre-fill the traversal with evenly spaced beads (random phase), so
    // the flow appears already running instead of starting empty.
    const phase = Math.random() * interval
    for (let t = now - (flow.len / BEAD_SPEED) * 1000 + phase; t <= now; t += interval) {
      live.push({ flow, t0: t })
    }
    return { flow, interval, next: now + phase }
  })

  const tick = (t) => {
    for (const e of emitters) {
      while (e.next <= t) {
        live.push({ flow: e.flow, t0: e.next })
        e.next += e.interval
      }
    }
    const out = []
    for (let i = live.length - 1; i >= 0; i--) {
      const b = live[i]
      const p = ((t - b.t0) / 1000) * BEAD_SPEED / b.flow.len
      if (p >= 1) {
        live.splice(i, 1)
        continue
      }
      out.push({
        x: b.flow.x1 + (b.flow.x2 - b.flow.x1) * p,
        y: b.flow.y1 + (b.flow.y2 - b.flow.y1) * p,
      })
    }
    beads.value = out
    rafId = requestAnimationFrame(tick)
  }
  rafId = requestAnimationFrame(tick)
}

watch(() => graph.value?.flows, startBeads, { immediate: true })
onBeforeUnmount(() => cancelAnimationFrame(rafId))

// Text in the graph must render at a constant screen size regardless of
// how far the enlarged graph's viewBox is scaled down to fit the panel:
// measure the unit→pixel ratio and expose it as --u on the svg, which the
// font-size rules divide by. Falls back to 1 (raw units) until measured.
const svgEl = ref(null)
const pxPerUnit = ref(1)
let resizeObs = null

function updateScale() {
  const el = svgEl.value
  if (el && el.viewBox.baseVal.width) {
    pxPerUnit.value = el.clientWidth / el.viewBox.baseVal.width
  }
}

onMounted(() => {
  resizeObs = new ResizeObserver(updateScale)
})
watch(svgEl, (el) => {
  resizeObs?.disconnect()
  if (el) resizeObs?.observe(el)
})
watch(() => graph.value?.bounds, updateScale)
onBeforeUnmount(() => resizeObs?.disconnect())

// Font size (px) that fits a label inside the pill width at the current
// zoom: ~0.52 em average glyph width, 12 px padding per side, capped.
const fitPx = (label) =>
  Math.min(15, (TNODE_W * pxPerUnit.value - 24) / (0.52 * Math.max(label.length, 1)))
</script>

<template>
  <section v-if="graph">
    <svg ref="svgEl" class="tmap" :style="{ '--u': pxPerUnit }" :viewBox="`${graph.bounds.x0} ${graph.bounds.y0} ${graph.bounds.x1 - graph.bounds.x0} ${graph.bounds.y1 - graph.bounds.y0}`"
         role="img" aria-label="map of transitions between pages">
      <path v-for="(a, i) in graph.arcs" :key="'a' + i"
            :d="a.d" class="tarc" />
      <path v-for="(e, i) in graph.edges" :key="'e' + i"
            :d="e.d" :class="['tconn', e.external && 'tconn-exit']">
        <title>{{ e.title }}</title>
      </path>
      <circle v-for="(b, i) in beads" :key="'b' + i"
              :cx="b.x" :cy="b.y" :r="BEAD_R" class="tbead" />
      <g v-for="(x, i) in graph.extNodes" :key="'x' + i">
        <a v-if="x.href" :href="x.href" target="_blank" rel="noopener">
          <title>{{ x.path }}</title>
          <rect :x="x.x - TNODE_W/2" :y="x.y - TNODE_H/2" :width="TNODE_W" :height="TNODE_H" :rx="TNODE_H/2"
                :class="['txnode', x.kind === 'source' ? 'txnode-source' : 'txnode-exit']" />
          <text :x="x.x" :y="x.y - TNODE_H*0.16" class="tnodeslug" dominant-baseline="middle"
                :style="{ '--slug-px': `${fitPx(x.label)}px` }">{{ x.label }}</text>
          <text :x="x.x" :y="x.y + TNODE_H*0.24" class="tnodecount" dominant-baseline="middle">{{ formatCount(x.count) }}</text>
        </a>
        <g v-else>
          <title>{{ x.path }}</title>
          <rect :x="x.x - TNODE_W/2" :y="x.y - TNODE_H/2" :width="TNODE_W" :height="TNODE_H" :rx="TNODE_H/2"
                :class="['txnode', x.kind === 'source' ? 'txnode-source' : 'txnode-exit']" />
          <text :x="x.x" :y="x.y - TNODE_H*0.16" class="tnodeslug" dominant-baseline="middle"
                :style="{ '--slug-px': `${fitPx(x.label)}px` }">{{ x.label }}</text>
          <text :x="x.x" :y="x.y + TNODE_H*0.24" class="tnodecount" dominant-baseline="middle">{{ formatCount(x.count) }}</text>
        </g>
      </g>
      <g v-for="n in graph.nodes" :key="n.path">
        <a :href="n.path">
          <title>{{ n.title }}</title>
          <rect :x="n.x - TNODE_W/2" :y="n.y - TNODE_H/2" :width="TNODE_W" :height="TNODE_H" :rx="TNODE_H/2" class="tnode" />
          <text :x="n.x" :y="n.y - TNODE_H*0.16" class="tnodeslug" dominant-baseline="middle"
                :style="{ '--slug-px': `${fitPx(n.label)}px` }">{{ n.label }}</text>
          <text :x="n.x" :y="n.y + TNODE_H*0.24" class="tnodecount" dominant-baseline="middle">
            {{ n.readMin ? `${formatCount(n.views)}×${n.readMin}m` : formatCount(n.views) }}
          </text>
        </a>
        <text v-if="n.crumb" :x="n.x" :y="n.y - TNODE_H/2 - 10"
              :class="['tnodepath', n.path === '/' && 'tnodepath-home']">{{ n.crumb }}</text>
      </g>
    </svg>
  </section>
</template>

<style scoped>
/* Transition map: radial graph of internal page-to-page transitions. */
.tmap {
  display: block;
  width: 100%;
  max-width: 100%;
}
.tmap .tconn {
  fill: var(--accent);
  opacity: 0.4; /* uniform, not strength-encoded: width carries that */
}
.tmap .tconn-exit {
  fill: var(--text);
}
.tmap .tbead {
  fill: var(--accent);
  opacity: 0.85;
  filter: drop-shadow(0 0 2.5px var(--accent));
}
.tmap .txnode {
  fill: var(--text);
  stroke: none;
}
.tmap .txnode-source { fill: var(--text); }
.tmap .txnode-exit { fill: var(--text); }
.tmap .tarc {
  fill: none;
  stroke: var(--line);
  stroke-width: 1;
}
.tmap .tnode {
  fill: var(--accent);
  stroke: none;
}
/* Text renders at a constant screen size: --u (set from JS) is the
   viewBox-unit → pixel ratio of the rendered svg, so dividing by it makes
   the sizes independent of how far the graph is scaled down. */
.tmap .tnodeslug {
  fill: var(--bg, Canvas);
  font-size: calc(var(--slug-px, 15px) / var(--u, 1));
  text-anchor: middle;
}
.tmap a { cursor: pointer; }
.tmap .tnodecount {
  fill: var(--bg, Canvas);
  opacity: 0.75;
  font-size: calc(13px / var(--u, 1));
  text-anchor: middle;
}
.tmap .tnodepath {
  fill: var(--muted);
  font-size: calc(11px / var(--u, 1));
  text-anchor: middle;
}
.tmap .tnodepath-home { font-size: calc(17px / var(--u, 1)); }

section { margin-top: 1.8rem; }
</style>
