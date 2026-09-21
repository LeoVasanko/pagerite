/**
 * Seasonal "typical week" estimate from the full visit history.
 *
 * Port of the seasonal.py demo algorithm: the whole smoothed 5-minute
 * history is collapsed onto a weekly grid with exponential decay over age —
 * a fast kernel (half-life 7 days) for the average time-of-day pattern and
 * a slow one (half-life 42 days) for per-weekday deviations from it. The
 * deviation is shrunk by the effective number of weeks behind each bin
 * (n_eff / (n_eff + 3)), so with little history the estimate falls back to
 * the common daily pattern and weekday character emerges as data accrues.
 * Bins before the first recorded bucket are treated as missing.
 */

import { DAY, MIN5, mondayUTC, rawTimes } from './time.js'
import { smooth } from './chart.js'

export const BINS_PER_DAY = 288
export const BINS_PER_WEEK = 7 * BINS_PER_DAY

// History cap: at 180 days the slow kernel's weight is 2^(-180/42) ≈ 5%
// (and the fast kernel's utterly negligible), so older data cannot move
// this noisy estimate — skipping it keeps the smoothing pass O(1).
const MAX_HISTORY_DAYS = 180

/**
 * Estimate the typical week from a dense 5-minute count series (oldest
 * first; non-finite values count as missing). endWeekBin is the week bin
 * (Monday-first) just past the last sample. Returns BINS_PER_WEEK counts
 * per 5-minute bin, starting Monday 00:00.
 */
export function seasonalCurve(counts, {
  endWeekBin,
  binsPerDay = BINS_PER_DAY,
  recentHalfLife = 7,
  weekdayHalfLife = 42,
  shrinkWeeks = 3,
} = {}) {
  const n = counts.length
  const binsPerWeek = 7 * binsPerDay

  const recentW = new Float64Array(binsPerDay)
  const recentX = new Float64Array(binsPerDay)
  const dayW = new Float64Array(binsPerDay)
  const dayX = new Float64Array(binsPerDay)
  const weekW = new Float64Array(binsPerWeek)
  const weekX = new Float64Array(binsPerWeek)
  const weekW2 = new Float64Array(binsPerWeek)

  for (let i = 0; i < n; i++) {
    const x = counts[i]
    if (!Number.isFinite(x)) continue
    let weekBin = (endWeekBin - n + i) % binsPerWeek
    if (weekBin < 0) weekBin += binsPerWeek
    const dayBin = weekBin % binsPerDay
    const ageDays = (n - 1 - i) / binsPerDay
    const recent = 2 ** (-ageDays / recentHalfLife)
    const slow = 2 ** (-ageDays / weekdayHalfLife)
    recentW[dayBin] += recent
    recentX[dayBin] += recent * x
    dayW[dayBin] += slow
    dayX[dayBin] += slow * x
    weekW[weekBin] += slow
    weekX[weekBin] += slow * x
    weekW2[weekBin] += slow * slow
  }

  const estimate = new Float64Array(binsPerWeek)
  for (let wb = 0; wb < binsPerWeek; wb++) {
    const db = wb % binsPerDay
    const recentMean = recentW[db] > 0 ? recentX[db] / recentW[db] : NaN
    const dayMean = dayW[db] > 0 ? dayX[db] / dayW[db] : NaN
    const weekMean = weekW[wb] > 0 ? weekX[wb] / weekW[wb] : 0
    const nEff = weekW2[wb] > 0 ? (weekW[wb] * weekW[wb]) / weekW2[wb] : 0
    const shrink = nEff / (nEff + shrinkWeeks)
    const base = Number.isFinite(recentMean)
      ? recentMean
      : Number.isFinite(dayMean) ? dayMean : 0
    const deviation = Number.isFinite(dayMean) ? weekMean - dayMean : 0
    estimate[wb] = base + shrink * deviation
  }
  return estimate
}

/** Week bin (0 = Monday 00:00–00:05 UTC) containing timestamp t. */
export function weekBinIndex(t) {
  return Math.floor((t - mondayUTC(t)) / MIN5)
}

/**
 * Typical-week estimate from sparse 5-minute buckets, using history up to
 * tEnd (default now): bins are densified from the first recorded bucket
 * (capped at MAX_HISTORY_DAYS back), smoothed with the same Gaussian the
 * week view uses, then folded by seasonalCurve. Returns BINS_PER_WEEK
 * counts per 5-minute bin starting Monday, or null when there is less than
 * a day of history or the history span (first bucket to tEnd, before
 * capping) is below minHistory.
 */
export function typicalWeek(buckets, tEnd = Date.now(), { minHistory = 0 } = {}) {
  const raw = rawTimes(buckets)
  const times = Object.keys(raw).map(Number)
  if (!times.length) return null
  const end = Math.floor(tEnd / MIN5) * MIN5
  if (end - Math.min(...times) < minHistory) return null
  const start = Math.max(Math.min(...times), end - MAX_HISTORY_DAYS * DAY)
  const n = Math.floor((end - start) / MIN5)
  if (n < BINS_PER_DAY) return null
  const counts = new Array(n)
  for (let i = 0; i < n; i++) counts[i] = raw[start + i * MIN5] || 0
  const smoothed = smooth(counts, 5, 60)
  return seasonalCurve(smoothed, { endWeekBin: weekBinIndex(end) })
}
