/**
 * Chart geometry, smoothing, and SVG path generation for analytics charts.
 *
 * Fixed 720x180 viewBox, stretched to the panel width; values are per-unit
 * rates (hour on the week view, day on month+).
 */

import { DAY, HOUR, WEEK, mondayUTC } from './time.js'

export const CHART_W = 720
export const CHART_H = 180
export const PAD_TOP = 14 // room above the highest point

/**
 * Y always starts at 0; the max is a multiple of a 1-2-5 major step with at
 * most 5 intervals, so labeled ticks are always round and evenly divided.
 * A minimum range of 10 keeps tiny near-zero values (e.g. a single visit)
 * from being enlarged to a fractional scale; minor lines subdivide each
 * major step in five when that yields integers.
 */
export function yScale(maxValue) {
  let step = 1
  outer: for (let exp = -3; exp < 8; exp++) {
    for (const base of [1, 2, 5]) {
      step = base * 10 ** exp
      if (Math.ceil(maxValue / step) <= 5) break outer
    }
  }
  let max = Math.ceil(maxValue / step) * step
  if (max < 10) {
    max = 10
    step = 2
  }
  const minor = step >= 5 && step % 5 === 0 ? step / 5 : null
  return { max, step, minor }
}

/**
 * Edge-aware adaptive Gaussian smoothing. A change-point detector first
 * finds traffic-level shifts (two-unit totals compared on both sides of
 * each bucket; strong ratio + significance marks a candidate, and each run
 * of candidates keeps only its best-scoring bucket as an edge). Each
 * edge-delimited segment is then smoothed independently: a broad two-unit
 * pilot estimates the local traffic rate, which ramps the Gaussian sigma
 * from ~0.4 units (isolated events stay narrow, peaking at ~1 event/unit)
 * up to 1 unit (busy traffic gets full smoothing), and every bucket spreads
 * its count with its local sigma, clipped to the segment and renormalized
 * so total visitor count is preserved exactly. The unit is one hour on the
 * week view and one day on the month+ views, so the smoothing time scale
 * follows the range (month+ sigmas are 24x the hourly ones). The raw series
 * is drawn faintly behind the curve for reference. Operates on raw counts.
 */
export function smooth(counts, binMinutes, unitMinutes, {
  minSigmaMinutes = unitMinutes / Math.sqrt(2 * Math.PI),
  maxSigmaMinutes = unitMinutes,
  pilotSigmaMinutes = 2 * unitMinutes,
  detectorWindowMinutes = 2 * unitMinutes,
  // Count thresholds are defined per hour and scale with the unit, so
  // "low traffic" means the same thing on hourly and daily views
  // (5-20 events/hour = 120-480/day on the month+ ranges).
  highTrafficEvents = 10 * unitMinutes / 60,
  minRatio = 2.5,
  minSignificance = 4,
  sigmaRampStart = 5 * unitMinutes / 60,
  sigmaRampEnd = 20 * unitMinutes / 60,
} = {}) {
  const n = counts.length
  if (!n) return counts
  const detectorWindowBins = Math.max(1, Math.round(detectorWindowMinutes / binMinutes))

  const cumsum = new Float64Array(n + 1)
  for (let i = 0; i < n; i++) cumsum[i + 1] = cumsum[i] + counts[i]

  // Detect abrupt regime changes from aggregated traffic on both sides.
  // Individual bins are deliberately ignored because even high traffic
  // produces many 0-1 count bins at five-minute resolution.
  const score = new Float64Array(n)
  const candidate = new Uint8Array(n)
  for (let i = detectorWindowBins; i < n - detectorWindowBins; i++) {
    const left = cumsum[i] - cumsum[i - detectorWindowBins]
    const right = cumsum[i + detectorWindowBins] - cumsum[i]
    const high = Math.max(left, right)
    const low = Math.min(left, right)
    if (high < highTrafficEvents) continue
    const ratio = (high + 1) / (low + 1)
    const significance = (high - low) / Math.sqrt(high + low + 1)
    if (ratio >= minRatio && significance >= minSignificance) {
      candidate[i] = 1
      score[i] = significance * Math.log(ratio)
    }
  }

  // Collapse each continuous detector region to its strongest boundary.
  const edges = []
  for (let i = 0; i < n;) {
    if (!candidate[i]) { i++; continue }
    let j = i + 1
    while (j < n && candidate[j]) j++
    let best = i
    for (let k = i + 1; k < j; k++) {
      if (score[k] > score[best]) best = k
    }
    edges.push(best)
    i = j
  }

  const reflectIndex = (i, length) => {
    while (i < 0 || i >= length) {
      i = i < 0 ? -i - 1 : 2 * length - i - 1
    }
    return i
  }

  const gaussianFilterReflect = (values, sigmaBins) => {
    const length = values.length
    const radius = Math.ceil(4 * sigmaBins)
    const kernel = new Float64Array(radius * 2 + 1)
    let sum = 0
    for (let k = -radius; k <= radius; k++) {
      const w = Math.exp(-0.5 * (k / sigmaBins) ** 2)
      kernel[k + radius] = w
      sum += w
    }
    for (let i = 0; i < kernel.length; i++) kernel[i] /= sum
    const out = new Float64Array(length)
    for (let i = 0; i < length; i++) {
      let value = 0
      for (let k = -radius; k <= radius; k++) {
        value += values[reflectIndex(i + k, length)] * kernel[k + radius]
      }
      out[i] = value
    }
    return out
  }

  // Process each discontinuity-delimited regime independently so neither
  // the pilot nor the final Gaussian can see through a detected boundary.
  const bounds = [0, ...edges, n]
  const smoothed = new Float64Array(n)
  for (let b = 0; b < bounds.length - 1; b++) {
    const lo = bounds[b]
    const length = bounds[b + 1] - lo
    const segment = counts.slice(lo, lo + length)

    // Broad pilot estimates only the generic local traffic level used for
    // choosing sigma; it is not the final displayed curve.
    const pilot = gaussianFilterReflect(segment, pilotSigmaMinutes / binMinutes)

    // Keep isolated/sparse traffic at the minimum bandwidth through
    // sigmaRampStart events, then ramp toward maxSigmaMinutes (thresholds
    // are per-hour rates scaled to the unit: low traffic is low traffic
    // on every range).
    const sigmaMinutes = new Float64Array(length)
    for (let i = 0; i < length; i++) {
      const ratePerUnit = pilot[i] * unitMinutes / binMinutes
      let mix = (ratePerUnit - sigmaRampStart) / (sigmaRampEnd - sigmaRampStart)
      mix = Math.sqrt(Math.max(0, Math.min(1, mix)))
      sigmaMinutes[i] = minSigmaMinutes + mix * (maxSigmaMinutes - minSigmaMinutes)
    }

    // Each input bin spreads its own count using its local sigma. The
    // per-bin kernel is renormalized after clipping to the segment,
    // preserving total visitor count apart from floating-point error.
    for (let j = 0; j < length; j++) {
      const count = segment[j]
      if (!count) continue
      const sigmaBins = sigmaMinutes[j] / binMinutes
      const radius = Math.ceil(4 * sigmaBins)
      const start = Math.max(0, j - radius)
      const end = Math.min(length, j + radius + 1)
      let weightSum = 0
      for (let i = start; i < end; i++) {
        const d = i - j
        weightSum += Math.exp(-0.5 * (d / sigmaBins) ** 2)
      }
      for (let i = start; i < end; i++) {
        const d = i - j
        smoothed[lo + i] += count * Math.exp(-0.5 * (d / sigmaBins) ** 2) / weightSum
      }
    }
  }
  return [...smoothed]
}

/**
 * Catmull-Rom spline through the (smoothed) points, control points clamped
 * to the plot area so the curve can never dip below zero or above the max.
 */
export function spline(pts) {
  if (pts.length < 3) {
    return `M${pts.map((p) => `${p.x},${p.y}`).join('L')}`
  }
  const clampY = (y) => Math.min(CHART_H, Math.max(PAD_TOP, y))
  let d = `M${pts[0].x},${pts[0].y}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[i + 2] || p2
    const c1y = clampY(p1.y + (p2.y - p0.y) / 6)
    const c2y = clampY(p2.y - (p3.y - p1.y) / 6)
    d += `C${p1.x + (p2.x - p0.x) / 6},${c1y} `
       + `${p2.x - (p3.x - p1.x) / 6},${c2y} ${p2.x},${p2.y}`
  }
  return d
}

/** Build a full chart model from a series descriptor produced by time.js. */
export function buildChart(input) {
  if (!input || !input.series.length) return null
  const { series, t0, t1, rate, binMinutes, unitMinutes } = input
  // Values are per-unit rates (hour on the week view, day on month+); the
  // y max is derived from the *smoothed* curves so random single-bucket
  // spikes don't blow up the scale. Smoothing works on raw counts (its edge
  // detector thresholds are count-based), the result is scaled back to rates.
  const smoothed = series.map((s) =>
    smooth(s.points.map((p) => p.count), binMinutes, unitMinutes).map((v) => v * rate))
  // Scale from the current/primary series only; older overlay weeks are drawn
  // with the same scale and allowed to overflow if they are busier.
  const highest = Math.max(0, ...smoothed[0])
  const { max, step, minor } = yScale(highest)
  const x = (t) => ((t - t0) / (t1 - t0)) * CHART_W
  const y = (v) => PAD_TOP + (1 - Math.max(0, v) / max) * (CHART_H - PAD_TOP)
  const drawn = series.map((s, si) => {
    const pts = s.points.map((p, i) => ({ x: x(p.t), y: y(smoothed[si][i]) }))
    const line = spline(pts)
    const first = pts[0]
    const last = pts.at(-1)
    return {
      ...s,
      line,
      area: s.area ? `${line}L${last.x},${CHART_H}L${first.x},${CHART_H}Z` : null,
    }
  })
  // Major (labeled) and minor (hairline) y grid ticks.
  const majors = []
  const minors = []
  const nMajor = Math.round(max / step)
  for (let k = 0; k <= nMajor; k++) {
    const v = k * step
    majors.push({ value: v, y: y(v), bottom: (1 - PAD_TOP / CHART_H) * (v / max) * 100 })
  }
  if (minor) {
    for (let v = minor; v < max; v += minor) {
      if (v % step !== 0) minors.push({ y: y(v) })
    }
  }
  // X ticks. Week view: weekday labels centered at midday UTC, no vertical
  // lines (day boundaries would be misleading in the viewer's timezone).
  // Month view: likewise lineless, day numbers at noon UTC with the month
  // name substituted for the 1st (marking the month change). Longer
  // ranges: boundary lines at Mondays / months / years.
  const isWeek = t1 - t0 === WEEK
  const isMonth = !isWeek && t1 - t0 <= 31 * DAY
  const xticks = isWeek
    ? Array.from({ length: 7 }, (_, d) => {
        const t = t0 + d * DAY + 12 * HOUR
        return {
          x: x(t), left: ((t - t0) / (t1 - t0)) * 100,
          label: new Date(t).toLocaleDateString(undefined, {
            weekday: 'short', timeZone: 'UTC',
          }),
          line: false,
        }
      })
    : isMonth
      ? Array.from(
          { length: Math.floor((t1 - Math.ceil(t0 / DAY) * DAY) / DAY) },
          (_, d) => {
            const day = Math.ceil(t0 / DAY) * DAY + d * DAY
            const date = new Date(day)
            const t = day + 12 * HOUR
            return {
              x: x(t), left: ((t - t0) / (t1 - t0)) * 100,
              label: date.getUTCDate() === 1
                ? date.toLocaleDateString(undefined, { month: 'short', timeZone: 'UTC' })
                : String(date.getUTCDate()),
              line: false,
            }
          },
        )
      : xticksFor(t0, t1).map((t) => ({
          x: x(t), left: ((t - t0) / (t1 - t0)) * 100,
          label: fmtTick(t, t1 - t0), line: true,
        }))
  return { max, majors, minors, series: drawn, xticks }
}

/** X ticks for year/all: Monday boundaries up to a quarter, UTC month
 *  boundaries up to a few years, then years. */
export function xticksFor(t0, t1) {
  const span = t1 - t0
  const ticks = []
  if (span <= 100 * DAY) {
    for (let t = mondayUTC(t0); t <= t1; t += WEEK) {
      if (t >= t0) ticks.push(t)
    }
    return ticks
  }
  if (span <= 4 * 365 * DAY) {
    const d = new Date(t0)
    let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)
    for (; t <= t1; ) {
      ticks.push(t)
      const m = new Date(t)
      t = Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1)
    }
    return ticks
  }
  const d = new Date(t0)
  for (let yr = d.getUTCFullYear() + 1; Date.UTC(yr, 0, 1) <= t1; yr++) {
    ticks.push(Date.UTC(yr, 0, 1))
  }
  return ticks
}

export function fmtTick(t, span) {
  const d = new Date(t)
  if (span <= 100 * DAY) {
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
  }
  if (span <= 4 * 365 * DAY) {
    return d.getUTCMonth() === 0
      ? d.toLocaleDateString(undefined, { year: 'numeric', timeZone: 'UTC' })
      : d.toLocaleDateString(undefined, { month: 'short', timeZone: 'UTC' })
  }
  return d.toLocaleDateString(undefined, { year: 'numeric', timeZone: 'UTC' })
}

/** Y labels: integers when the step allows, one decimal for fractional steps. */
export function fmtY(v) {
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}
