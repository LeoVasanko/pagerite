<script setup>
/**
 * Visitor and page-view smoothed curves for a single shared time range.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { makeSeries } from './analytics/time.js'
import { typicalWeek, weekBinIndex } from './analytics/seasonal.js'
import {
  CHART_H,
  CHART_W,
  MARGIN_B,
  MARGIN_L,
  VIEW_H,
  VIEW_W,
  buildChart,
} from './analytics/chart.js'

const DAY_REFRESH_MS = 15000

// Keep the whole svg within page bounds: full width below the natural
// size, centered with equal side margins above it (max() clamps the
// centering margin to 0 at the breakpoint, so the rule is continuous).
const CHART_MARGIN = `max(0px, calc(50% - ${VIEW_W / 2}px))`

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

function freqLabel(unit) {
  return unit === '5min' ? '5 min' : unit === 'hour' ? 'hourly' : 'daily'
}

/** Vertical axis caption: "visits / 5 min" on the day view, else "hourly visits" style. */
function axisLabel(unit, ylabel) {
  return unit === '5min' ? `${ylabel} / 5 min` : `${freqLabel(unit)} ${ylabel}`
}

const now = ref(Date.now())
let refreshInterval = null
onMounted(() => {
  refreshInterval = setInterval(() => { now.value = Date.now() }, DAY_REFRESH_MS)
})
onUnmounted(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})

/**
 * Series for the current range plus, on the day and week views, the
 * seasonal "typical week" history curve (all history up to now, already
 * smoothed). Week view: the full Monday-first week. Day view: the rolling
 * window's bins looked up from the same estimate, labeled by the weekday.
 */
function withTypical(buckets) {
  const input = makeSeries(buckets, props.range)
  if (props.range !== 'day' && props.range !== 'week') return input
  const estimate = typicalWeek(buckets, now.value)
  if (!estimate) return input
  if (props.range === 'week') {
    return { ...input, typical: { values: [...estimate], label: 'Typical week' } }
  }
  const values = input.series[0].points.map((p) => estimate[weekBinIndex(p.t)])
  const weekday = new Date(now.value).toLocaleDateString(undefined, {
    weekday: 'long', timeZone: 'UTC',
  })
  return { ...input, typical: { values, label: `Typical ${weekday}` } }
}

const visitChart = computed(() => buildChart(withTypical(props.data?.site_visits), now.value))
const viewChart = computed(() => buildChart(withTypical(allViews.value), now.value))
</script>

<template>
  <section v-for="c in [
      { ylabel: 'visits', chart: visitChart, legend: true },
      { ylabel: 'views', chart: viewChart, legend: false },
    ]" :key="c.ylabel">
    <template v-if="c.chart">
      <svg class="chart" :viewBox="`${-MARGIN_L} 0 ${VIEW_W} ${VIEW_H}`"
           :style="{ maxWidth: `${VIEW_W}px`, marginLeft: CHART_MARGIN }"
           role="img" :aria-label="axisLabel(c.chart.unit, c.ylabel)">
        <!-- Clip the plot curves to the chart area; the svg itself is
             overflow: visible for the axis labels. -->
        <clipPath :id="`plot-${c.ylabel}`">
          <rect x="0" y="0" :width="CHART_W" :height="CHART_H" />
        </clipPath>
        <line v-for="g in c.chart.majors.slice(1)" :key="'j' + g.value"
              :x1="0" :x2="CHART_W" :y1="g.y" :y2="g.y" class="major" />
        <template v-for="t in c.chart.xticks" :key="'t' + t.x">
          <line v-if="t.line" :x1="t.x" :x2="t.x" :y1="0" :y2="CHART_H"
                class="minor vertical" />
        </template>
        <g :clip-path="`url(#plot-${c.ylabel})`">
          <!-- The muted "typical" history curve under the current data. -->
          <path v-if="c.chart.typical" :d="c.chart.typical.line" class="line past" />
          <template v-if="c.chart.bars">
            <rect v-for="(b, i) in c.chart.bars" :key="'b' + i"
                  :x="b.x" :y="b.y" :width="b.width" :height="b.height" class="bar" />
            <path :d="c.chart.skyline" class="line" />
          </template>
          <template v-else>
            <template v-for="(s, i) in c.chart.series" :key="i">
              <path v-if="s.area" :d="s.area" class="area" />
              <path :d="s.line" class="line" />
            </template>
          </template>
        </g>
        <line :x1="0" :x2="CHART_W" :y1="CHART_H - 0.5" :y2="CHART_H - 0.5"
              class="axis" />
        <text v-for="g in c.chart.majors" :key="'y' + g.value" x="-5" :y="g.y"
              text-anchor="end" dominant-baseline="middle" class="ylab">{{ g.label }}</text>
        <text :x="-(MARGIN_L - 10)" :y="CHART_H / 2" text-anchor="middle"
              :transform="`rotate(-90 ${-(MARGIN_L - 10)} ${CHART_H / 2})`"
              class="yaxis-label">{{ axisLabel(c.chart.unit, c.ylabel) }}</text>
        <text v-for="t in c.chart.xticks" :key="'x' + t.x" :x="t.x" :y="CHART_H + MARGIN_B - 8"
              text-anchor="middle" class="xlab">{{ t.label }}</text>
        <!-- Legend, top right inside the plot: current data in accent (week
             curve or day bars) and the typical history curve in muted. -->
        <g v-if="c.legend && c.chart.typical">
          <template v-if="c.chart.bars">
            <rect :x="CHART_W - 118" y="5" width="20" height="9" class="bar" />
            <text :x="CHART_W - 92" y="10" dominant-baseline="middle"
                  class="leglab">Last 24 hours</text>
          </template>
          <template v-else>
            <line :x1="CHART_W - 118" :x2="CHART_W - 98" y1="10" y2="10" class="line" />
            <text :x="CHART_W - 92" y="10" dominant-baseline="middle"
                  class="leglab">{{ c.chart.series[0].label }}</text>
          </template>
          <line :x1="CHART_W - 118" :x2="CHART_W - 98" y1="25" y2="25"
                class="line past" style="opacity: 0.6" />
          <text :x="CHART_W - 92" y="25" dominant-baseline="middle"
                class="leglab">{{ c.chart.typical.label }}</text>
        </g>
      </svg>
    </template>
  </section>
</template>

<style scoped>
/* Each chart is a self-contained SVG: the viewBox includes the axis label
   margins, so nothing is positioned with HTML overlays. Never upscale past
   the natural size (1 viewBox unit = 1 px, max-width set inline) — that
   would blow up the constant-size text; smaller panels still scale the
   chart down to fit. The margin-left (set inline) centers the chart above
   its natural width; the svg always stays within page bounds.
   overflow: visible lets wider fonts extend past the viewBox instead of
   clipping. */
.chart {
  display: block;
  width: 100%;
  height: auto;
  overflow: visible;
}

.chart .ylab,
.chart .xlab,
.chart .yaxis-label,
.chart .leglab {
  font-family: system-ui, sans-serif; /* theme fonts can be overly styled */
  font-size: 11px;
  fill: var(--muted);
}

.chart .ylab {
  font-variant-numeric: tabular-nums;
}

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

/* Past overlay weeks contrast with the current week's accent color. */
.chart .line.past {
  stroke: var(--muted);
}

.empty { color: var(--muted); }
</style>
