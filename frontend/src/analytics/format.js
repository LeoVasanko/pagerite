/**
 * Formatters and aggregators for summary sections: totals and the recent
 * visit trail.
 */

/**
 * IPv4 unchanged, IPv6 returns the /64 network prefix in compact form.
 * Falls back to the original value when parsing fails.
 */
export const hostIP = (ip) => {
  try {
    if (!ip || !ip.includes(':')) return ip
    const strip = (s) => s.replace(/^\[|\]$/g, '')
    const norm = strip(new URL(`http://[${ip}]/`).hostname)
    const [l, r] = norm.split('::').map((s) => (s ? s.split(':') : []))
    const full = r
      ? [...l, ...Array(8 - l.length - r.length).fill('0'), ...r]
      : l
    return strip(
      new URL(`http://[${full.slice(0, 4).join(':')}::]/`).hostname,
    ).replace(/::$/, '')
  } catch (e) {
    console.error('hostIP processing failed for:', ip, e)
    return ip
  }
}

/** Copy the full IP to the clipboard, ignoring failures. */
export async function copyIp(ip) {
  if (!ip) return
  try {
    await navigator.clipboard.writeText(ip)
  } catch {
    /* ignore */
  }
}

/** Total page views across every page and every bucket. */
export function calcTotalViews(views) {
  let n = 0
  for (const buckets of Object.values(views || {})) {
    for (const c of Object.values(buckets)) n += c
  }
  return n
}

/** Build a path -> page title lookup from the site tree. */
function buildTitleMap(pageTree) {
  const titles = new Map()
  const walk = (items) => {
    for (const item of items || []) {
      titles.set(`/${item.path}`, item.title)
      walk(item.children)
    }
  }
  walk(pageTree)
  return titles
}

/** Last path segment for display; front page becomes a house icon. */
function slugOf(path) {
  return path === '/' ? '🏠' : path.split('/').pop()
}

/** Host name of an external https origin, with scheme stripped. */
function externalSlug(origin) {
  try {
    return new URL(origin).host
  } catch {
    return origin.replace(/^https?:\/\//, '')
  }
}

/** Format one trail step: an internal page or an external https origin. */
function stepOf(path, titles) {
  if (path?.startsWith('/')) {
    return { path, slug: slugOf(path), title: titles.get(path) || '', external: false }
  }
  if (path?.startsWith('https://')) {
    return {
      path,
      slug: externalSlug(path),
      title: 'External site',
      external: true,
    }
  }
  return null
}

/**
 * Human-readable relative timestamp.  Adapted from cista-storage: uses
 * ``Intl.RelativeTimeFormat`` for short intervals and a compact date for
 * anything older than a week.
 */
export function formatWhen(ts, now = Date.now()) {
  const date = new Date(ts)
  const diff = date.getTime() - now
  const adiff = Math.abs(diff)
  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  if (adiff <= 5000) return 'now'
  if (adiff <= 60000) {
    return formatter
      .format(Math.round(diff / 1000), 'second')
      .replace(' ago', '')
      .replaceAll(' ', '\u202F')
  }
  if (adiff <= 3600000) {
    return formatter
      .format(Math.round(diff / 60000), 'minute')
      .replace('utes', '')
      .replace('ute', '')
      .replaceAll(' ', '\u202F')
  }
  if (adiff <= 86400000) {
    return formatter
      .format(Math.round(diff / 3600000), 'hour')
      .replaceAll(' ', '\u202F')
  }
  if (adiff <= 604800000) {
    return formatter
      .format(Math.round(diff / 86400000), 'day')
      .replaceAll(' ', '\u202F')
  }
  let d = date
    .toLocaleDateString('en-ie', {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
    .replace('Sept', 'Sep')
  if (d.length === 14) d = d.replace(' ', ' \u2007')
  d = d.replaceAll(' ', '\u202F').replace('\u202F', '\u00A0')
  d = d.slice(0, -4) + d.slice(-2)
  return d
}

/** Full UTC timestamp for tooltips, e.g. "2026-08-21 00:20:48 UTC". */
export function formatWhenTooltip(ts) {
  return new Date(ts).toISOString().replace('T', ' ').replace('Z', ' UTC')
}

/**
 * Format recent visits for display, newest first. Each step is a linked slug
 * pointing to its article; external referers/origins are shown as their
 * domain name with the full origin as the link href. The link title shows the
 * article heading when known, or "External site" for origins.
 */
export function formatRecentVisits(visits, pageTree, limit = 50) {
  const titles = buildTitleMap(pageTree)
  return [...visits]
    .reverse()
    .map((v) => ({
      when: new Date(v.start).toLocaleString(),
      steps: [v.referer, v.entry, ...(v.trail || [])]
        .map((p) => stepOf(p, titles))
        .filter(Boolean),
    }))
    .filter((v) => v.steps.length)
    .slice(0, limit)
}

/**
 * Count distinct values of a visit field, sorted most-common first.
 * Returns an array of [value, count] pairs.
 */
export function countByField(visits, field) {
  const counts = {}
  for (const v of visits || []) {
    const value = v[field]
    if (!value) continue
    counts[value] = (counts[value] || 0) + 1
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
}

/**
 * Count UTM parameter occurrences across visits.  Each distinct
 * ``parameter: value`` pair is counted separately.  Returns [pair, count].
 */
export function countUtmTags(visits) {
  const counts = {}
  for (const v of visits || []) {
    for (const [key, value] of Object.entries(v.utm || {})) {
      const label = `${key}: ${value}`
      counts[label] = (counts[label] || 0) + 1
    }
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
}

/** Format a list of [value, count] pairs for inline display. */
export function formatCounts(entries) {
  return entries.map(([value, count]) => `${value} (${count})`).join(', ')
}

/**
 * Count distinct User-Agent strings among crawler hits, most common first.
 * Returns an array of [ua, count] pairs.
 */
export function countCrawlerUas(crawlers) {
  const counts = {}
  for (const c of crawlers || []) {
    const value = c.ua_pretty || c.ua || '(no UA)'
    counts[value] = (counts[value] || 0) + 1
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
}

/**
 * Group raw crawler hits by the same (ip, ua) pair we use to tell a real
 * visitor from a crawler, and format each group as a row showing every
 * internal page that crawler visited.  Rows are sorted by total hits,
 * most active crawler first, rather than by most recent hit.
 */
export function formatCrawlerRows(crawlers, pageTree, now = Date.now()) {
  const titles = buildTitleMap(pageTree)
  const groups = new Map()
  for (const c of crawlers || []) {
    const key = `${c.ip}\0${c.ua}`
    const g = groups.get(key) || {
      ip: c.ip || '',
      ua: c.ua_pretty || c.ua || '—',
      uaRaw: c.ua || '',
      lastStart: 0,
      pages: new Map(),
    }
    const start = new Date(c.start).getTime()
    if (start > g.lastStart) g.lastStart = start
    if (c.entry?.startsWith('/')) {
      g.pages.set(c.entry, (g.pages.get(c.entry) || 0) + 1)
    }
    groups.set(key, g)
  }
  const totalHits = (g) => {
    let n = 0
    for (const c of g.pages.values()) n += c
    return n
  }
  return [...groups.values()]
    .sort((a, b) => totalHits(b) - totalHits(a) || b.lastStart - a.lastStart)
    .slice(0, 10)
    .map((g) => ({
      when: formatWhen(g.lastStart, now),
      whenTooltip: formatWhenTooltip(g.lastStart),
      pages: [...g.pages.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([path, count]) => ({
          path,
          slug: slugOf(path),
          title: titles.get(path) || '',
          count,
        })),
      ip: g.ip,
      ipDisplay: hostIP(g.ip) || g.ip || '—',
      ua: g.ua,
      uaRaw: g.uaRaw,
      total: totalHits(g),
    }))
}

/**
 * Format raw visit records as rows for a technical table.  Returns objects
 * with display strings; missing values become "—".  ``trail`` starts with the
 * external referer (when present), then the entry page and any further internal
 * pages or external exit origins. Only the 20 most recent visits are shown.
 */
export function formatVisitRows(visits, pageTree, now = Date.now()) {
  const titles = buildTitleMap(pageTree)
  return [...(visits || [])].reverse().slice(0, 20).map((v) => {
    const trail = [v.referer, v.entry, ...(v.trail || [])]
      .map((p) => stepOf(p, titles))
      .filter(Boolean)
    const utm = Object.entries(v.utm || {})
      .map(([k, value]) => `${k}=${value}`)
      .join(', ')
    const dash = (s) => (s || '—')
    return {
      when: formatWhen(v.start, now),
      whenTooltip: formatWhenTooltip(v.start),
      trail,
      referer: dash(v.referer),
      ip: v.ip || '',
      ipDisplay: v.host || hostIP(v.ip) || v.ip || '—',
      host: dash(v.host),
      lang: dash(v.lang),
      country: dash(v.country),
      city: dash(v.city),
      ua: v.ua_pretty || v.ua || '—',
      uaRaw: v.ua || '',
      utm: utm || '—',
    }
  })
}
