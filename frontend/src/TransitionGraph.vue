<script setup>
/**
 * Radial transition map filtered to the selected time range.
 *
 * The server only stores an all-time transition aggregate, so this component
 * derives time-filtered transitions from the visits list (which has start
 * timestamps) and filters the view counts to the same window.
 */
import { computed } from 'vue'
import { rangeWindow } from './analytics/time.js'
import {
  TNODE_R,
  buildTransitionGraph,
  buildTransitionsFromVisits,
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
    transitions: buildTransitionsFromVisits(props.data.visits, t0, t1),
    views: filterViewsByRange(props.data.views, t0, t1),
  }
})

const graph = computed(() =>
  filteredData.value
    ? buildTransitionGraph(filteredData.value, props.pageTree)
    : null,
)
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
