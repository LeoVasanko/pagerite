<script setup>
/**
 * Radial transition map filtered to the selected time range.
 *
 * Transitions are stored per 5-minute bucket (from -> to -> bucket ->
 * count), so the graph sums the buckets falling inside the selected
 * range, exactly like the charts and per-page views do.
 */
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { rangeWindow, WEEK } from './analytics/time.js'
import { formatCount } from './analytics/format.js'
import {
  TNODE_R,
  BEAD_R,
  BEAD_SPEED,
  buildTransitionGraph,
  filterTransitionsByRange,
  filterViewsByRange,
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

const graph = computed(() =>
  filteredData.value
    ? buildTransitionGraph(filteredData.value, props.pageTree, props.data?.visits, visualScale.value)
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
</script>

<template>
  <section v-if="graph">
    <svg class="tmap" :viewBox="`${graph.bounds.x0} ${graph.bounds.y0} ${graph.bounds.x1 - graph.bounds.x0} ${graph.bounds.y1 - graph.bounds.y0}`"
         role="img" aria-label="map of transitions between pages">
      <defs>
        <!-- Unit-radius circle; only the portion near the bottom is used. -->
        <path id="tnode-label-arc" d="M 0,-1 A 1,1 0 1,0 0,1 A 1,1 0 1,0 -0.001,-1" />
      </defs>
      <path v-for="(a, i) in graph.arcs" :key="'a' + i"
            :d="a.d" class="tarc" />
      <path v-for="(e, i) in graph.edges" :key="'e' + i"
            :d="e.d" :class="['tconn', e.external && 'tconn-exit']">
        <title>{{ e.title }}</title>
      </path>
      <circle v-for="(b, i) in beads" :key="'b' + i"
              :cx="b.x" :cy="b.y" :r="BEAD_R" class="tbead" />
      <g v-for="(x, i) in graph.extNodes" :key="'x' + i">
        <a :href="x.path" target="_blank" rel="noopener" :title="x.path">
          <circle :cx="x.x" :cy="x.y" :r="x.r"
                  :class="['txnode', x.kind === 'source' ? 'txnode-source' : 'txnode-exit']" />
          <text :transform="`translate(${x.x}, ${x.y}) scale(${x.r - 4})`" class="tnodeslug" :style="{ '--node-r': x.r - 4 }">
            <textPath href="#tnode-label-arc" startOffset="50%" text-anchor="middle" side="right">{{ x.label }}</textPath>
          </text>
          <text :x="x.x" :y="x.y + 4" class="tnodecount">{{ formatCount(x.count) }}</text>
        </a>
      </g>
      <g v-for="n in graph.nodes" :key="n.path">
        <a v-if="!n.hidden" :href="n.path" :title="n.title">
          <circle :cx="n.x" :cy="n.y" :r="TNODE_R" class="tnode" />
          <text :transform="`translate(${n.x}, ${n.y}) scale(${TNODE_R - 4})`" class="tnodeslug" :style="{ '--node-r': TNODE_R - 4 }">
            <textPath href="#tnode-label-arc" startOffset="50%" text-anchor="middle" side="right">{{ n.label }}</textPath>
          </text>
          <text :x="n.x" :y="n.y + 4" class="tnodecount">
            {{ n.readMin ? `${formatCount(n.views)}×${n.readMin}m` : formatCount(n.views) }}
          </text>
        </a>
        <template v-else>
          <text :x="n.x" :y="n.y"
                :transform="`rotate(${n.angle * 180 / Math.PI}, ${n.x}, ${n.y})`"
                class="tnodehidden" text-anchor="start" dominant-baseline="middle">➤</text>
          <text :x="n.x + Math.cos(n.angle) * 10"
                :y="n.y + Math.sin(n.angle) * 10"
                :transform="`rotate(${(n.angle + (Math.cos(n.angle) < 0 ? Math.PI : 0)) * 180 / Math.PI}, ${n.x + Math.cos(n.angle) * 10}, ${n.y + Math.sin(n.angle) * 10})`"
                :text-anchor="Math.cos(n.angle) < 0 ? 'end' : 'start'"
                class="tnodehidden" dominant-baseline="middle">{{ n.label }}</text>
        </template>
      </g>
    </svg>
  </section>
</template>

<style scoped>
/* Transition map: radial graph of internal page-to-page transitions. */
.tmap {
  display: block;
  width: 100%;
  max-width: 36rem;
  margin: 0 auto;
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
  fill: var(--bg, Canvas);
  stroke-width: 1.5;
}
.tmap .txnode-source { stroke: var(--text); }
.tmap .txnode-exit { stroke: var(--text); }
.tmap .tarc {
  fill: none;
  stroke: var(--line);
  stroke-width: 1;
}
.tmap .tnode {
  fill: var(--bg, Canvas);
  stroke: var(--accent);
  stroke-width: 1.5;
}
.tmap .tnodeslug {
  fill: var(--text);
  font-size: calc(11px / var(--node-r, 34));
  text-anchor: middle;
}
.tmap a { cursor: pointer; }
.tmap a:hover .tnodeslug { fill: var(--accent); }
.tmap .tnodecount {
  fill: var(--muted);
  font-size: 10px;
  text-anchor: middle;
}
.tmap .tnodehidden {
  fill: var(--text);
  font-size: 9px;
}

section { margin-top: 1.8rem; }
</style>
