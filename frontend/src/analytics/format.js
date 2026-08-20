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

/**
 * Format recent visits for display, newest first. Each step is a linked slug
 * pointing to its article; external referers/origins and direct entries are
 * omitted. The link title shows the article heading when known.
 */
export function formatRecentVisits(visits, pageTree, limit = 50) {
  const titles = buildTitleMap(pageTree)
  return [...visits]
    .reverse()
    .map((v) => ({
      when: new Date(v.start).toLocaleString(),
      steps: [v.entry, ...(v.trail || [])]
        .filter((p) => p?.startsWith('/'))
        .map((p) => ({
          path: p,
          slug: slugOf(p),
          title: titles.get(p) || '',
        })),
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
 * Format raw crawler hit records as rows for a technical table.  Missing
 * values become "—".
 */
export function formatCrawlerRows(crawlers) {
  const dash = (s) => (s || '—')
  return [...(crawlers || [])].reverse().map((c) => ({
    when: new Date(c.start).toLocaleString(),
    entry: dash(c.entry),
    ip: c.ip || '',
    ipDisplay: c.host || hostIP(c.ip) || c.ip || '—',
    ua: c.ua_pretty || c.ua || '—',
    uaRaw: c.ua || '',
    referer: dash(c.referer),
    query: dash(c.query),
  }))
}

/**
 * Format raw visit records as rows for a technical table.  Returns objects
 * with display strings; missing values become "—".  ``trail`` joins page
 * titles (when known) with " -> ".
 */
export function formatVisitRows(visits, pageTree) {
  const titles = buildTitleMap(pageTree)
  return [...(visits || [])].reverse().map((v) => {
    const trail = [v.entry, ...(v.trail || [])]
      .filter((p) => p?.startsWith('/'))
      .map((p) => ({
        path: p,
        slug: slugOf(p),
        title: titles.get(p) || '',
      }))
    const utm = Object.entries(v.utm || {})
      .map(([k, value]) => `${k}=${value}`)
      .join(', ')
    const dash = (s) => (s || '—')
    return {
      when: new Date(v.start).toLocaleString(),
      trail,
      referer: dash(v.referer),
      ip: v.ip || '',
      ipDisplay: v.host || hostIP(v.ip) || v.ip || '—',
      host: dash(v.host),
      lang: dash(v.lang),
      country: dash(v.country),
      ua: v.ua_pretty || v.ua || '—',
      uaRaw: v.ua || '',
      utm: utm || '—',
    }
  })
}
