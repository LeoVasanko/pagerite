/**
 * Time ranges, week alignment and re-bucketing for analytics charts.
 *
 * Raw data comes as sparse 5-minute buckets; the range picks the x window
 * and a coarser bucket size to keep point counts sane. The week range is
 * aligned to Monday 00:00 UTC and overlays previous weeks' curves (fading
 * with age), so weekly patterns compare directly.
 */

export const MIN5 = 5 * 60e3
export const HOUR = 3600e3
export const DAY = 86400e3
export const WEEK = 7 * DAY

export const RANGES = {
  day: { label: 'day', span: DAY, bucket: MIN5 },
  week: { label: 'week' },
  month: { label: 'month', span: 30 * DAY, bucket: 6 * HOUR },
  year: { label: 'year', span: 365 * DAY, bucket: DAY },
  all: { label: 'all', span: null, bucket: DAY, minSpan: 30 * DAY },
}

/** Monday 00:00 UTC of the week containing t (epoch day 0 was a Thursday). */
export function mondayUTC(t) {
  const d = Math.floor(t / DAY)
  return (d - ((d + 3) % 7)) * DAY
}

/** Parse sparse timestamp buckets into a { epochMs: count } map. */
export function rawTimes(buckets) {
  const raw = {}
  // Key by parsed timestamp: Python writes "+00:00", JS ISO uses "Z".
  for (const [k, c] of Object.entries(buckets || {})) raw[Date.parse(k)] = c
  return raw
}

/** Sum counts from raw 5-minute buckets between t0 (inclusive) and t1 (exclusive). */
export function sumRange(raw, t0, t1) {
  let n = 0
  for (let s = t0; s < t1; s += MIN5) n += raw[s] || 0
  return n
}

/**
 * One series per overlaid week: [this week, 1 week ago, ...], at native
 * 5-minute resolution, up to 8 weeks back (and only weeks that overlap the
 * recorded data at all). The current week is truncated at the current bucket
 * — no fake zeroes drawn for the future. Counts are rates per hour
 * (bucket count * 12): a lone visit in a 5-minute bucket reads as "12/h".
 * The coarser ranges use per-day rates instead (unitMinutes = 24*60).
 */
export function weeklySeries(buckets) {
  const raw = rawTimes(buckets)
  const times = Object.keys(raw).map(Number)
  if (!times.length) return null
  const now = Date.now()
  const thisMonday = mondayUTC(now)
  const oldest = Math.min(...times)
  // Weeks back as far as the data reaches: difference in Monday indices.
  const available = (thisMonday - mondayUTC(oldest)) / WEEK + 1
  const count = Math.min(available, 8)
  const out = []
  for (let back = 0; back < count; back++) {
    const start = thisMonday - back * WEEK
    const end = back === 0
      ? Math.min(start + WEEK, Math.floor(now / MIN5) * MIN5 + MIN5)
      : start + WEEK
    const points = []
    for (let t = start; t < end; t += MIN5) {
      points.push({ t, count: raw[t] || 0 })
    }
    out.push({
      points,
      label: back === 0 ? 'this week' : `${back}w ago`,
      opacity: Math.max(0.15, 1 - back * 0.25),
      area: back === 0,
    })
  }
  return {
    series: out,
    t0: thisMonday,
    t1: thisMonday + WEEK,
    rate: HOUR / MIN5,
    binMinutes: 5,
    unitMinutes: 60,
    unit: 'hour',
  }
}

/**
 * Rolling window for the non-week ranges (x max = now), counts converted
 * to per-day rates (the unit the month+ charts are read in).
 * Ranges without a fixed span use the full data reach, but never less than
 * their configured minSpan so the chart keeps a readable minimum x scale.
 */
export function rollingSeries(buckets, rangeKey) {
  const raw = rawTimes(buckets)
  const times = Object.keys(raw).map(Number)
  if (!times.length) return null
  const { span, bucket, minSpan = 0 } = RANGES[rangeKey]
  const t1 = Math.floor(Date.now() / bucket) * bucket + bucket
  const earliest = Math.floor(Math.min(...times) / bucket) * bucket
  const t0 = span != null
    ? t1 - span
    : Math.min(earliest, t1 - minSpan)
  const points = []
  for (let t = t0; t < t1; t += bucket) {
    points.push({ t, count: sumRange(raw, t, t + bucket) })
  }
  return {
    series: [{ points, label: '', opacity: 1, area: true }],
    t0,
    t1,
    rate: DAY / bucket,
    binMinutes: bucket / 60e3,
    unitMinutes: 24 * 60,
    unit: 'day',
  }
}

/**
 * Day view: raw 5-minute bucket counts for the current 24-hour window.
 * No smoothing or rate conversion is applied; counts are used as-is.
 */
export function daySeries(buckets) {
  const raw = rawTimes(buckets)
  const now = Date.now()
  const { span, bucket } = RANGES.day
  const t1 = Math.floor(now / bucket) * bucket + bucket
  const t0 = t1 - span
  const points = []
  for (let t = t0; t < t1; t += bucket) {
    points.push({ t, count: raw[t] || 0 })
  }
  return {
    series: [{ points, label: '', opacity: 1, area: false }],
    t0,
    t1,
    rate: 1,
    binMinutes: bucket / 60e3,
    unitMinutes: bucket / 60e3,
    unit: '5min',
  }
}

/** Dispatch to daily, weekly or rolling series based on the selected range. */
export function makeSeries(buckets, rangeKey) {
  if (rangeKey === 'day') return daySeries(buckets)
  if (rangeKey === 'week') return weeklySeries(buckets)
  return rollingSeries(buckets, rangeKey)
}

/**
 * Absolute UTC time window for a given range key. Used to filter visits,
 * transitions and views for the non-chart stats on the analytics page.
 * The week range is a rolling 7 days ending at now; the charts instead
 * align to Monday 00:00 UTC and overlay previous weeks, so their x window
 * differs from the stats range on purpose.
 * Returns { t0, t1 } where null means unbounded.
 */
export function rangeWindow(rangeKey) {
  const now = Date.now()
  if (rangeKey === 'week') {
    return { t0: now - WEEK, t1: now }
  }
  if (rangeKey === 'all') {
    return { t0: null, t1: null }
  }
  const { span, bucket } = RANGES[rangeKey]
  const t1 = Math.floor(now / bucket) * bucket + bucket
  return { t0: t1 - span, t1 }
}

/**
 * Sum the bucketed transition matrix (from -> to -> bucket ISO -> count)
 * into a plain from -> to -> count matrix for the window [t0, t1).
 */
export function filterTransitionsByRange(transitions, t0, t1) {
  const out = {}
  for (const [fr, tos] of Object.entries(transitions || {})) {
    for (const [to, buckets] of Object.entries(tos)) {
      let n = 0
      for (const [k, c] of Object.entries(buckets)) {
        const t = Date.parse(k)
        if ((t0 == null || t >= t0) && (t1 == null || t < t1)) n += c
      }
      if (n) {
        out[fr] = out[fr] || {}
        out[fr][to] = n
      }
    }
  }
  return out
}

/** Keep only the 5-minute view buckets that fall inside [t0, t1). */
export function filterViewsByRange(views, t0, t1) {
  const filtered = {}
  for (const [path, buckets] of Object.entries(views || {})) {
    const out = {}
    for (const [k, c] of Object.entries(buckets)) {
      const t = Date.parse(k)
      if ((t0 == null || t >= t0) && (t1 == null || t < t1)) out[k] = c
    }
    if (Object.keys(out).length) filtered[path] = out
  }
  return filtered
}

/** Keep only records whose start time falls inside [t0, t1). */
export function filterRecordsByRange(records, t0, t1) {
  const out = []
  for (const r of records || []) {
    const t = Date.parse(r.start)
    if ((t0 == null || t >= t0) && (t1 == null || t < t1)) out.push(r)
  }
  return out
}
