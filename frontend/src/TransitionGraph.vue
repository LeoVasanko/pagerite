<script setup>
/**
 * Radial transition map filtered to the selected time range.
 *
 * Transitions are stored per 5-minute bucket (from -> to -> bucket ->
 * count), so the graph sums the buckets falling inside the selected
 * range, exactly like the charts and per-page views do.
 */
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { rangeWindow } from './analytics/time.js'
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
const emit = defineEmits(['close'])

const window = computed(() => rangeWindow(props.range))

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
    ? buildTransitionGraph(filteredData.value, props.pageTree)
    : null,
)

// Bead animation: every bead is simulated independently in JS. Each flow
// (one per edge direction) emits a bead every `interval` seconds; beads
// travel at BEAD_SPEED along the segment and are dropped at the end.
// There is deliberately no cap on beads in flight.
const beads = shallowRef([])
let rafId = 0

const startBeads = (flows) => {
  cancelAnimationFrame(rafId)
  beads.value = []
  if (!flows?.length) return
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return

  const live = [] // { flow, t0 } — one entry per bead in flight
  const now = performance.now()
  const emitters = flows.map((flow) => {
    const interval = flow.interval * 1000
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
      <path v-for="(a, i) in graph.arcs" :key="'a' + i"
            :d="a.d" class="tarc" />
      <path v-for="(e, i) in graph.edges" :key="'e' + i"
            :d="e.d" class="tconn">
        <title>{{ e.title }}</title>
      </path>
      <circle v-for="(b, i) in beads" :key="'b' + i"
              :cx="b.x" :cy="b.y" :r="BEAD_R" class="tbead" />
      <g v-for="(x, i) in graph.extNodes" :key="'x' + i">
        <circle :cx="x.x" :cy="x.y" :r="x.r" class="txnode">
          <title>{{ x.path }}</title>
        </circle>
        <text :x="x.x" :y="x.y + x.r + 11" class="txlabel">{{ x.label }}</text>
      </g>
      <g v-for="n in graph.nodes" :key="n.path">
        <a :href="n.path" :title="n.title" @click="emit('close')">
          <circle :cx="n.x" :cy="n.y" :r="TNODE_R" class="tnode" />
          <text :x="n.x" :y="n.y - 2" class="tnodeslug">{{ n.label }}</text>
          <text :x="n.x" :y="n.y + 12" class="tnodecount">{{ n.views }}</text>
        </a>
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
.tmap .tbead {
  fill: var(--accent);
  opacity: 0.85;
  filter: drop-shadow(0 0 2.5px var(--accent));
}
.tmap .txnode {
  fill: var(--bg, Canvas);
  stroke: var(--muted);
  stroke-width: 1;
}
.tmap .txlabel {
  fill: var(--muted);
  font-size: 9px;
  text-anchor: middle;
}
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
  font-size: 11px;
  text-anchor: middle;
}
.tmap a { cursor: pointer; }
.tmap a:hover .tnodeslug { fill: var(--accent); }
.tmap .tnodecount {
  fill: var(--muted);
  font-size: 10px;
  text-anchor: middle;
}

section { margin-top: 1.8rem; }
</style>
