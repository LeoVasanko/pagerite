<script setup>
/**
 * Radial transition map for a pre-filtered time range.
 *
 * The parent filters transitions, views and visits to the selected range
 * before passing them in; `window` carries the absolute [t0, t1) window
 * so the visual scale can normalize against a one-week reference.
 */
import { computed, onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import { DAY } from './analytics/time.js'
import { formatCount, formatReadTime } from './analytics/format.js'
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
  favicons: { type: Object, default: null },
})

// origin -> /_f/... icon URL, keyed by the node's origin (source/exit
// pills only; UTM-tagged source nodes without an https origin stay
// text-only).
const extFavicon = (x) => {
  if (!x.path?.startsWith('https://')) return null
  try {
    return props.favicons?.[new URL(x.path).origin] || null
  } catch {
    return null
  }
}

const dayScale = computed(() => {
  const { t0, t1 } = props.window
  // Convert raw counts to a daily hit rate (hits/day).
  if (t0 != null && t1 != null) return DAY / (t1 - t0)
  // 'all': scale by the actual data span, but never less than the 30-day
  // minimum the plot enforces, so sparse young data is not over-amplified.
  const times = new Set()
  for (const buckets of Object.values(props.data?.views || {})) {
    for (const k of Object.keys(buckets)) times.add(Date.parse(k))
  }
  const arr = [...times]
  if (arr.length < 2) return 1
  const span = Math.max(...arr) - Math.min(...arr)
  return DAY / Math.max(span, 30 * DAY)
})

const graph = computed(() =>
  props.data
    ? buildTransitionGraph(props.data, props.pageTree, props.data.visits || [], dayScale.value)
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
const TRAVERSAL_S = 0.4 // seconds to cross any segment, end to end

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
// units, and upscaling would blow up the pills around their text. Narrow
// panels scale the graph down to fit (width: 100%), text along with it.

// Pill text is not truncated: text is clipped at the pill's rounded border
// (clipPath per node, inset a few units for padding). Captions center when
// they fit; overlong ones anchor left so their beginning (not their
// middle) survives the clip. Width estimate: ~0.52 em per glyph.
const fitsPill = (label, fontPx = 19) => label.length * 0.52 * fontPx <= TNODE_W - 16

// With a favicon the label leaves room for the icon at the pill's left
// and is always left-anchored past it.
const labelX = (x) =>
  extFavicon(x) ? x.x - TNODE_W / 2 + 36 : fitsPill(x.label) ? x.x : x.x - TNODE_W / 2 + 8
const labelAnchor = (x) => (!extFavicon(x) && fitsPill(x.label) ? 'middle' : 'start')

const countLabel = (n) =>
  n.readSec ? `${formatCount(n.views)}×${formatReadTime(n.readSec)}` : formatCount(n.views)
</script>

<template>
  <section v-if="graph">
    <svg class="tmap" :style="{ maxWidth: `${graph.bounds.x1 - graph.bounds.x0}px` }" :viewBox="`${graph.bounds.x0} ${graph.bounds.y0} ${graph.bounds.x1 - graph.bounds.x0} ${graph.bounds.y1 - graph.bounds.y0}`"
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
        <clipPath :id="`xclip${i}`">
          <rect :x="x.x - TNODE_W/2 + 6" :y="x.y - TNODE_H/2" :width="TNODE_W - 12"
                :height="TNODE_H" :rx="TNODE_H/2 - 4" />
        </clipPath>
        <a v-if="x.href" :href="x.href" target="_blank" rel="noopener">
          <title>{{ x.path }}</title>
          <rect :x="x.x - TNODE_W/2" :y="x.y - TNODE_H/2" :width="TNODE_W" :height="TNODE_H" :rx="TNODE_H/2"
                :class="['txnode', x.kind === 'source' ? 'txnode-source' : 'txnode-exit']" />
          <g :clip-path="`url(#xclip${i})`">
            <image v-if="extFavicon(x)" :href="extFavicon(x)" :x="x.x - TNODE_W/2 + 12" :y="x.y - TNODE_H*0.16 - 11" width="22" height="22" />
            <text :x="labelX(x)" :y="x.y - TNODE_H*0.16" class="tnodeslug" dominant-baseline="middle" :style="{ textAnchor: labelAnchor(x) }">{{ x.label }}</text>
            <text :x="x.x" :y="x.y + TNODE_H*0.24" class="tnodecount" dominant-baseline="middle">{{ formatCount(x.count) }}</text>
          </g>
        </a>
        <g v-else>
          <title>{{ x.path }}</title>
          <rect :x="x.x - TNODE_W/2" :y="x.y - TNODE_H/2" :width="TNODE_W" :height="TNODE_H" :rx="TNODE_H/2"
                :class="['txnode', x.kind === 'source' ? 'txnode-source' : 'txnode-exit']" />
          <g :clip-path="`url(#xclip${i})`">
            <image v-if="extFavicon(x)" :href="extFavicon(x)" :x="x.x - TNODE_W/2 + 12" :y="x.y - TNODE_H*0.16 - 11" width="22" height="22" />
            <text :x="labelX(x)" :y="x.y - TNODE_H*0.16" class="tnodeslug" dominant-baseline="middle" :style="{ textAnchor: labelAnchor(x) }">{{ x.label }}</text>
            <text :x="x.x" :y="x.y + TNODE_H*0.24" class="tnodecount" dominant-baseline="middle">{{ formatCount(x.count) }}</text>
          </g>
        </g>
      </g>
      <g v-for="(n, i) in graph.nodes" :key="n.path">
        <clipPath :id="`nclip${i}`">
          <rect :x="n.x - TNODE_W/2 + 6" :y="n.y - TNODE_H/2" :width="TNODE_W - 12"
                :height="TNODE_H" :rx="TNODE_H/2 - 4" />
        </clipPath>
        <a :href="n.path">
          <title>{{ n.title }}</title>
          <rect :x="n.x - TNODE_W/2" :y="n.y - TNODE_H/2" :width="TNODE_W" :height="TNODE_H" :rx="TNODE_H/2" class="tnode" />
          <g :clip-path="`url(#nclip${i})`">
            <text :x="fitsPill(n.label) ? n.x : n.x - TNODE_W/2 + 8" :y="n.y - TNODE_H*0.16" class="tnodeslug" dominant-baseline="middle" :style="{ textAnchor: fitsPill(n.label) ? 'middle' : 'start' }">{{ n.label }}</text>
            <text :x="n.x" :y="n.y + TNODE_H*0.24" class="tnodecount" dominant-baseline="middle">
              {{ countLabel(n) }}
            </text>
          </g>
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
  font-size: 13px;
  text-anchor: start;
}
/* The top lane is 50% thicker; its 🏠︎ label scales along. */
.tmap .tarclabel-top {
  font-size: 19.5px;
}
.tmap .tnode {
  fill: var(--accent);
  stroke: none;
}
/* Text sizes are viewBox units: they shrink along with the graph on
   narrow panels. Overlong labels are clipped at the pill border. */
.tmap .tnodeslug {
  fill: var(--bg, Canvas);
  font-size: 19px;
  text-anchor: start;
}
.tmap a { cursor: pointer; }
.tmap .tnodecount {
  fill: var(--bg, Canvas);
  opacity: 0.75;
  font-size: 15px;
  text-anchor: middle;
}

section { margin-top: 1.8rem; }
</style>
