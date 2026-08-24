<script setup>
/**
 * Radial transition map for a pre-filtered time range.
 *
 * The parent filters transitions, views and visits to the selected range
 * before passing them in; `window` carries the absolute [t0, t1) window
 * so the visual scale can normalize against a one-week reference.
 */
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { DAY, WEEK } from './analytics/time.js'
import { formatCount } from './analytics/format.js'
import {
  TNODE_W,
  TNODE_H,
  BEAD_R,
  buildTransitionGraph,
} from './analytics/transitions.js'

const props = defineProps({
  data: { type: Object, default: null },
  window: { type: Object, required: true },
  pageTree: { type: Array, default: null },
})

const visualScale = computed(() => {
  const { t0, t1 } = props.window
  if (t0 != null && t1 != null) return WEEK / (t1 - t0)
  // 'all': scale by the actual data span, but never less than the 30-day
  // minimum the plot enforces, so sparse young data is not over-amplified.
  const times = new Set()
  for (const buckets of Object.values(props.data?.views || {})) {
    for (const k of Object.keys(buckets)) times.add(Date.parse(k))
  }
  const arr = [...times]
  if (arr.length < 2) return 1
  const span = Math.max(...arr) - Math.min(...arr)
  return WEEK / Math.max(span, 30 * DAY)
})

const graph = computed(() =>
  props.data
    ? buildTransitionGraph(props.data, props.pageTree, props.data.visits || [], visualScale.value)
    : null,
)

// Bead animation: every bead is simulated independently in JS. Each flow
// (one per edge direction) emits a bead every `interval` seconds; beads
// cross their segment in a constant TRAVERSAL_S seconds (speed relative
// to span length) and are dropped at the end.
// There is deliberately no cap on beads in flight.
// Emitters persist across data reloads, keyed by flow.key: an unchanged
// link keeps its emission phase and in-flight beads (tracked by progress,
// not absolute time), so a count change elsewhere never reshuffles them.
const beads = shallowRef([])
let rafId = 0
const emitters = new Map() // flow.key -> { flow, interval, next, alive }
const live = [] // { e, p } — beads in flight, p = progress 0..1
let lastTick = 0

const MAX_BEAD_RATE = 120 // upper bound on total beads per second
const TRAVERSAL_S = 1.5 // seconds to cross any segment, end to end

const syncBeads = (flows) => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
  if (!flows?.length || reduced) {
    emitters.clear()
    live.length = 0
    beads.value = []
    return
  }
  // Cap the total bead emission rate so a busy range cannot spawn enough
  // beads to kill the page. Existing per-range time scaling is preserved;
  // this is only a proportional emergency throttle when the limit is hit.
  const totalRate = flows.reduce((s, f) => s + 1 / f.interval, 0)
  const scale = totalRate > MAX_BEAD_RATE ? MAX_BEAD_RATE / totalRate : 1

  const now = performance.now()
  const seen = new Set()
  for (const flow of flows) {
    seen.add(flow.key)
    const interval = (flow.interval / scale) * 1000
    const e = emitters.get(flow.key)
    if (e) {
      e.flow = flow // pick up new geometry/rate, keep the phase
      e.interval = interval
      continue
    }
    // New emitter: pre-fill the traversal with evenly spaced beads (random
    // phase), so the flow appears already running instead of empty.
    const phase = Math.random() * interval
    const dp = interval / 1000 / TRAVERSAL_S
    const ne = { flow, interval, next: now + phase, alive: true }
    for (let p = 1 - phase / 1000 / TRAVERSAL_S; p > 0; p -= dp) {
      live.push({ e: ne, p })
    }
    emitters.set(flow.key, ne)
  }
  for (const [key, e] of emitters) {
    if (!seen.has(key)) {
      e.alive = false
      emitters.delete(key)
    }
  }
  for (let i = live.length - 1; i >= 0; i--) {
    if (!live[i].e.alive) live.splice(i, 1)
  }
}

const tick = (t) => {
  const dt = lastTick ? (t - lastTick) / 1000 : 0
  lastTick = t
  for (const e of emitters.values()) {
    while (e.next <= t) {
      live.push({ e, p: 0 })
      e.next += e.interval
    }
  }
  const out = []
  for (let i = live.length - 1; i >= 0; i--) {
    const b = live[i]
    b.p += dt / TRAVERSAL_S
    if (b.p >= 1) {
      live.splice(i, 1)
      continue
    }
    const f = b.e.flow
    out.push({ x: f.x1 + (f.x2 - f.x1) * b.p, y: f.y1 + (f.y2 - f.y1) * b.p })
  }
  beads.value = out
  rafId = requestAnimationFrame(tick)
}

watch(() => graph.value?.flows, syncBeads, { immediate: true })
onMounted(() => {
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    rafId = requestAnimationFrame(tick)
  }
})
onBeforeUnmount(() => cancelAnimationFrame(rafId))

// The svg never renders larger than its natural size (1 viewBox unit = 1
// px, max-width below): the layout geometry is designed in pixel-like
// units, and upscaling would blow the pills up around their constant-size
// text. Narrow panels still scale the graph down to fit (width: 100%).
// Text renders at a constant screen size regardless of that downscale:
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

// Label text shortened to fit inside the pill at the current zoom (fonts
// are fixed screen sizes): drop whole trailing words first, then hard-cut
// with an ellipsis. Width estimate: ~0.52 em per glyph, 12 px padding
// per side.
const fitLabel = (label, fontPx = 15) => {
  const budget = Math.max(2, (TNODE_W * pxPerUnit.value - 24) / (0.52 * fontPx))
  if (label.length <= budget) return label
  const words = label.split(' ')
  while (words.length > 1 && words.join(' ').length + 1 > budget) words.pop()
  let out = words.join(' ')
  if (out.length + 1 > budget) out = out.slice(0, Math.floor(budget) - 1)
  return `${out}…`
}

const countLabel = (n) =>
  n.readMin ? `${formatCount(n.views)}×${n.readMin}m` : formatCount(n.views)
</script>

<template>
  <section v-if="graph">
    <svg ref="svgEl" class="tmap" :style="{ '--u': pxPerUnit, maxWidth: `${graph.bounds.x1 - graph.bounds.x0}px` }" :viewBox="`${graph.bounds.x0} ${graph.bounds.y0} ${graph.bounds.x1 - graph.bounds.x0} ${graph.bounds.y1 - graph.bounds.y0}`"
         role="img" aria-label="map of transitions between pages">
      <path v-for="(a, i) in graph.arcs" :key="'a' + i"
            :id="`tarc${i}`" :d="a.d" :class="['tarc', a.top && 'tarc-top']" />
      <template v-for="(a, i) in graph.arcs" :key="'t' + i">
        <path v-if="a.ld" :id="`tarcl${i}`" :d="a.ld" fill="none" stroke="none" />
        <text v-if="a.ld" class="tarclabel" :class="{ 'tarclabel-top': a.top }"><textPath :href="`#tarcl${i}`" startOffset="0">{{ a.label }}</textPath></text>
      </template>
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
          <text :x="x.x" :y="x.y - TNODE_H*0.16" class="tnodeslug" dominant-baseline="middle">{{ fitLabel(x.label) }}</text>
          <text :x="x.x" :y="x.y + TNODE_H*0.24" class="tnodecount" dominant-baseline="middle">{{ fitLabel(formatCount(x.count), 13) }}</text>
        </a>
        <g v-else>
          <title>{{ x.path }}</title>
          <rect :x="x.x - TNODE_W/2" :y="x.y - TNODE_H/2" :width="TNODE_W" :height="TNODE_H" :rx="TNODE_H/2"
                :class="['txnode', x.kind === 'source' ? 'txnode-source' : 'txnode-exit']" />
          <text :x="x.x" :y="x.y - TNODE_H*0.16" class="tnodeslug" dominant-baseline="middle">{{ fitLabel(x.label) }}</text>
          <text :x="x.x" :y="x.y + TNODE_H*0.24" class="tnodecount" dominant-baseline="middle">{{ fitLabel(formatCount(x.count), 13) }}</text>
        </g>
      </g>
      <g v-for="n in graph.nodes" :key="n.path">
        <a :href="n.path">
          <title>{{ n.title }}</title>
          <rect :x="n.x - TNODE_W/2" :y="n.y - TNODE_H/2" :width="TNODE_W" :height="TNODE_H" :rx="TNODE_H/2" class="tnode" />
          <text :x="n.x" :y="n.y - TNODE_H*0.16" class="tnodeslug" dominant-baseline="middle">{{ fitLabel(n.label) }}</text>
          <text :x="n.x" :y="n.y + TNODE_H*0.24" class="tnodecount" dominant-baseline="middle">
            {{ fitLabel(countLabel(n), 13) }}
          </text>
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
  /* max-width is set inline to the natural content width (px = viewBox
     units), so wide panels never upscale the graph beyond 1:1. */
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
  fill: var(--text);
  stroke: none;
}
.tmap .txnode-source { fill: var(--text); }
.tmap .txnode-exit { fill: var(--text); }
/* Branch lanes: one wide concentric arc per path prefix, running behind
   the node pills around the fan's circle center; parent levels sit one
   indent (radius step) outward. Each lane's label follows a short guide
   arc across the first inter-node gap (the part pills never cover). */
.tmap .tarc {
  fill: none;
  stroke: var(--muted);
  stroke-width: 16;
  opacity: 0.25;
}
.tmap .tarc-top { stroke-width: 24; }
/* Lane labels are left-aligned: each guide arc starts just past the source
   pill's edge, the earliest point where the text is visible. */
.tmap .tarclabel {
  fill: var(--muted);
  font-size: calc(13px / var(--u, 1));
  text-anchor: start;
}
/* The top lane is 50% thicker; its 🏠︎ label scales along. */
.tmap .tarclabel-top {
  font-size: calc(19.5px / var(--u, 1));
}
.tmap .tnode {
  fill: var(--accent);
  stroke: none;
}
/* Text renders at a constant screen size: --u (set from JS) is the
   viewBox-unit → pixel ratio of the rendered svg, so dividing by it makes
   the sizes independent of how far the graph is scaled down. Labels are
   shortened in JS to fit the pill instead of shrinking the font. */
.tmap .tnodeslug {
  fill: var(--bg, Canvas);
  font-size: calc(15px / var(--u, 1));
  text-anchor: middle;
}
.tmap a { cursor: pointer; }
.tmap .tnodecount {
  fill: var(--bg, Canvas);
  opacity: 0.75;
  font-size: calc(13px / var(--u, 1));
  text-anchor: middle;
}

section { margin-top: 1.8rem; }
</style>
