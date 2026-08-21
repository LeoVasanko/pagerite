<script setup>
/**
 * Visitor and page-view smoothed curves for a single shared time range.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { makeSeries } from './analytics/time.js'
import { CHART_H, CHART_W, buildChart, fmtY } from './analytics/chart.js'

const DAY_REFRESH_MS = 15000

const props = defineProps({
  data: { type: Object, default: null },
  range: { type: String, required: true },
})

// Views across all pages combined into one raw bucket map.
const allViews = computed(() => {
  const all = {}
  for (const buckets of Object.values(props.data?.views || {})) {
    for (const [k, c] of Object.entries(buckets)) all[k] = (all[k] || 0) + c
  }
  return all
})

const visitSeries = computed(() => makeSeries(props.data?.site_visits, props.range))
const viewSeries = computed(() => makeSeries(allViews.value, props.range))

function freqLabel(unit) {
  return unit === '5min' ? '5 min' : unit === 'hour' ? 'hourly' : 'daily'
}

const now = ref(Date.now())
let refreshInterval = null
onMounted(() => {
  refreshInterval = setInterval(() => { now.value = Date.now() }, DAY_REFRESH_MS)
})
onUnmounted(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})

const visitChart = computed(() => buildChart(visitSeries.value, now.value))
const viewChart = computed(() => buildChart(viewSeries.value, now.value))
</script>

<template>
  <section v-for="c in [
      { ylabel: 'visits', chart: visitChart, empty: 'no visits recorded yet' },
      { ylabel: 'views', chart: viewChart, empty: 'no views recorded yet' },
    ]" :key="c.ylabel">
    <template v-if="c.chart">
      <div class="chartwrap">
        <div class="plot">
          <div class="plotarea">
            <span class="yaxis-label">{{ freqLabel(c.chart.unit) }} {{ c.ylabel }}</span>
            <svg class="chart" :viewBox="`0 0 ${CHART_W} ${CHART_H}`"
                 preserveAspectRatio="none" role="img" :aria-label="`${freqLabel(c.chart.unit)} ${c.ylabel}`">
              <line v-for="g in c.chart.majors.slice(1)" :key="'j' + g.value"
                    :x1="0" :x2="CHART_W" :y1="g.y" :y2="g.y" class="major" />
              <template v-for="t in c.chart.xticks" :key="'t' + t.x">
                <line v-if="t.line" :x1="t.x" :x2="t.x" :y1="0" :y2="CHART_H"
                      class="minor vertical" />
              </template>
              <template v-if="c.chart.bars">
                <rect v-for="(b, i) in c.chart.bars" :key="'b' + i"
                      :x="b.x" :y="b.y" :width="b.width" :height="b.height" class="bar" />
                <path :d="c.chart.skyline" class="line" />
              </template>
              <template v-else>
                <template v-for="(s, i) in c.chart.series" :key="i">
                  <path v-if="s.area" :d="s.area" class="area" />
                  <path :d="s.line" class="line" :style="{ opacity: s.opacity }" />
                </template>
              </template>
              <line :x1="0" :x2="CHART_W" :y1="CHART_H - 0.5" :y2="CHART_H - 0.5"
                    class="axis" />
            </svg>
            <span v-for="g in c.chart.majors" :key="g.value" class="ylab"
                  :style="{ bottom: g.bottom + '%' }">{{ fmtY(g.value) }}</span>
          </div>
          <div class="xlabels">
            <span v-for="t in c.chart.xticks" :key="t.x" class="xlab"
                  :style="{ left: t.left + '%' }">{{ t.label }}</span>
          </div>
        </div>
      </div>
      <div v-if="c.chart.series && c.chart.series.length > 1" class="legend">
        <span v-for="(s, i) in c.chart.series" :key="i" :style="{ opacity: s.opacity }">
          ● {{ s.label }}
        </span>
      </div>
    </template>
    <p v-else class="empty">{{ c.empty }}</p>
  </section>
</template>

<style scoped>
/* The svg is stretched (preserveAspectRatio none), so all text lives in
   HTML overlays positioned by the same fractions the geometry uses. */
.chartwrap {
  padding-left: 2.8rem; /* y labels */
}

.plot {
  display: flex;
  flex-direction: column;
  width: 100%;
}

.plotarea {
  position: relative;
  height: 8rem;
}

.xlabels {
  position: relative;
  height: 1.2rem;
}

.chart {
  display: block;
  width: 100%;
  height: 100%;
}

.ylab {
  position: absolute;
  left: -2.8rem;
  width: 2.6rem;
  text-align: right;
  transform: translateY(50%);
  font-size: 0.7rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.xlab {
  position: absolute;
  top: 0.25rem;
  transform: translateX(-50%);
  font-size: 0.7rem;
  color: var(--muted);
  white-space: nowrap;
}

.xlabels .xlab:first-child { transform: none; }
.xlabels .xlab:last-child { transform: translateX(-100%); }

.chart .minor {
  stroke: var(--line);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
  opacity: 0.35;
}

.chart .minor.vertical {
  opacity: 0.25;
}

.chart .major {
  stroke: var(--line);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
  stroke-dasharray: 3 4;
  opacity: 0.8;
}

.chart .axis {
  stroke: var(--line);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

.chart .area {
  fill: var(--accent);
  opacity: 0.15;
}

.chart .bar {
  fill: var(--accent);
  opacity: 0.15;
}

.chart .line {
  fill: none;
  stroke: var(--accent);
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.legend {
  display: flex;
  gap: 1.2rem;
  margin-top: 0.4rem;
  font-size: 0.75rem;
  color: var(--muted);
}

.legend span { color: var(--accent); }

.yaxis-label {
  position: absolute;
  top: 50%;
  left: -2.8rem;
  font-size: 0.7rem;
  color: var(--muted);
  writing-mode: vertical-rl;
  white-space: nowrap;
  transform: translateY(-50%) rotate(180deg);
}

section { margin-top: 1.8rem; }

.empty { color: var(--muted); }
</style>
