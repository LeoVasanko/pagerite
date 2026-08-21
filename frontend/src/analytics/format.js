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

function showCopiedFeedback(el) {
  if (!el || typeof document === 'undefined') return
  const popup = document.createElement('span')
  popup.textContent = 'Copied!'
  popup.className = 'copy-popup'
  popup.style.cssText =
    'position:absolute;bottom:calc(100% + 0.25rem);left:50%;' +
    'transform:translateX(-50%);padding:0.15rem 0.4rem;' +
    'background:var(--text, CanvasText);color:var(--bg, Canvas);' +
    'border-radius:0.25rem;font-size:0.75rem;white-space:nowrap;' +
    'pointer-events:none;z-index:10;'
  el.classList.add('has-copy-popup')
  el.appendChild(popup)
  setTimeout(() => {
    popup.remove()
    el.classList.remove('has-copy-popup')
  }, 1200)
}

/** Copy the full IP to the clipboard and show a brief "Copied!" popup. */
export async function copyIp(ip, event) {
  if (!ip) return
  const el = event?.currentTarget
  try {
    await navigator.clipboard.writeText(ip)
    showCopiedFeedback(el)
  } catch {
    /* ignore */
  }
}

/** Copy arbitrary text to the clipboard and show a brief "Copied!" popup. */
export async function copyList(text, event) {
  if (!text) return
  const el = event?.currentTarget
  try {
    await navigator.clipboard.writeText(text)
    showCopiedFeedback(el)
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

// Very short reads are navigation/skims, not real reading time.
export const MIN_READ_SECONDS = 10

/** Average minutes per visit and average of per-article median read minutes. */
export function calcReadStats(visits) {
  const perArticle = {}
  let totalVisitSeconds = 0
  let visitCount = 0
  for (const v of visits || []) {
    const secs = Object.values(v.read || {}).filter((s) => s >= MIN_READ_SECONDS)
    if (!secs.length) continue
    visitCount++
    totalVisitSeconds += secs.reduce((a, b) => a + b, 0)
    for (const [path, s] of Object.entries(v.read || {})) {
      if (s >= MIN_READ_SECONDS) {
        ;(perArticle[path] || (perArticle[path] = [])).push(s)
      }
    }
  }
  const avgMinPerVisit = visitCount
    ? Math.max(1, Math.round(totalVisitSeconds / visitCount / 60))
    : 0

  let articleMedianSum = 0
  const articleCount = Object.keys(perArticle).length
  for (const arr of Object.values(perArticle)) {
    arr.sort((a, b) => a - b)
    const mid = Math.floor(arr.length / 2)
    const median = arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2
    articleMedianSum += Math.max(MIN_READ_SECONDS, median)
  }
  const avgArticleMedianMin = articleCount
    ? Math.max(1, Math.round(articleMedianSum / articleCount / 60))
    : 0

  return { avgMinPerVisit, avgArticleMedianMin }
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

/** Host name of an external https origin, with scheme and www. stripped. */
function externalSlug(origin) {
  try {
    return new URL(origin).host.replace(/^www\./, '')
  } catch {
    return origin.replace(/^https?:\/\//, '').replace(/^www\./, '')
  }
}

/** Format one trail step: an internal page or an external https origin. */
function stepOf(path, titles) {
  if (path?.startsWith('/')) {
    return { path, slug: slugOf(path), title: titles.get(path) || '', external: false, home: path === '/' }
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

/** Full local timestamp for tooltips, e.g. "21 Aug 2026, 17:38:48". */
export function formatWhenLocal(ts) {
  return new Date(ts).toLocaleString('en-ie', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

/** Preserve locale case with the region/country subtag upper-cased. */
export function formatLang(value) {
  if (!value || value === '—') return value
  const parts = value.split('-')
  if (parts.length > 1) {
    parts[parts.length - 1] = parts[parts.length - 1].toUpperCase()
  }
  return parts.join('-')
}

/** ISO 8601 UTC timestamp without subseconds, e.g. "2026-08-21T00:20:48Z". */
export function formatWhenIso(ts) {
  return `${new Date(ts).toISOString().split('.')[0]}Z`
}

/**
 * Compact visitor counts: plain below 1k, then 1.2k / 10k / 1.2M.
 * Truncated, not rounded.
 */
export function formatCount(n) {
  if (n < 1000) return String(n)
  if (n < 10000) return `${Math.trunc(n / 1000)}.${Math.trunc((n % 1000) / 100)}k`
  if (n < 1_000_000) return `${Math.trunc(n / 1000)}k`
  return `${Math.trunc(n / 1_000_000)}.${Math.trunc((n % 1_000_000) / 100_000)}M`
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
      lastSeen: formatWhen(g.lastStart, now),
      lastSeenIso: formatWhenIso(g.lastStart),
      lastSeenLocal: formatWhenLocal(g.lastStart),
      pages: [...g.pages.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([path, count]) => ({ ...stepOf(path, titles), count })),
      ip: g.ip,
      ipDisplay: hostIP(g.ip) || g.ip || '—',
      ua: g.ua,
      uaRaw: g.uaRaw,
      total: totalHits(g),
    }))
}

/**
 * Group abuse hits by IP (never by UA — scanners randomize theirs to
 * masquerade as legitimate crawlers) and format each group as a row with
 * the full paths probed.  Identical paths are collapsed into one entry
 * with their hit count.  Flagged paths (the ones that triggered abuse
 * classification) are lifted to the top, followed by other 404s, then
 * document GETs from the abuser.  Within each category paths are sorted by
 * count descending, then earliest first.  UAs are shown raw, one per line,
 * with their occurrence counts.  Paths are shown verbatim (query string
 * included), not resolved against the page tree.
 */
export function formatAbuseRows(abuse, now = Date.now()) {
  const groups = new Map()
  for (const a of abuse || []) {
    const g = groups.get(a.ip) || {
      ip: a.ip || '',
      pathCounts: new Map(),
      rawUas: [],
      uaCounts: new Map(),
      lastStart: 0,
    }
    const start = new Date(a.start).getTime()
    if (start > g.lastStart) g.lastStart = start
    const path = a.path || ''
    const existing = g.pathCounts.get(path) || {
      path,
      count: 0,
      firstStart: start,
      flag: a.flag || false,
      is_404: a.is_404 || false,
    }
    existing.count += 1
    if (start < existing.firstStart) existing.firstStart = start
    if (a.flag) existing.flag = true
    if (!a.is_404) existing.is_404 = false
    g.pathCounts.set(path, existing)
    const ua = a.ua || '(no UA)'
    g.rawUas.push(ua)
    g.uaCounts.set(ua, (g.uaCounts.get(ua) || 0) + 1)
    groups.set(a.ip, g)
  }
  const totalHits = (g) => {
    let n = 0
    for (const p of g.pathCounts.values()) n += p.count
    return n
  }
  return [...groups.values()]
    .sort((a, b) => totalHits(b) - totalHits(a) || b.lastStart - a.lastStart)
    .slice(0, 10)
    .map((g) => {
      const pathCategory = (p) => (p.flag ? 0 : p.is_404 ? 1 : 2)
      const paths = [...g.pathCounts.values()].sort(
        (a, b) =>
          pathCategory(a) - pathCategory(b) ||
          b.count - a.count ||
          a.firstStart - b.firstStart,
      )
      const uas = [...g.uaCounts.entries()]
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      return {
        lastSeen: formatWhen(g.lastStart, now),
        lastSeenIso: formatWhenIso(g.lastStart),
        lastSeenLocal: formatWhenLocal(g.lastStart),
        paths: paths.map((p) => ({
          path: p.path,
          count: p.count,
          flag: p.flag,
          is_404: p.is_404,
        })),
        allPaths: paths
          .map((p) => (p.count > 1 ? `${p.count}× ${p.path}` : p.path))
          .join('\n'),
        uas: uas.map(([ua, count]) => ({ ua, count })),
        allUas: uas
          .map(([ua, count]) => (count > 1 ? `${count}× ${ua}` : ua))
          .join('\n'),
        ip: g.ip,
        ipDisplay: hostIP(g.ip) || g.ip || '—',
        total: totalHits(g),
      }
    })
}

/**
 * Reduce a reverse-DNS hostname to its right-most components that fit
 * within ``limit`` characters.  This keeps the meaningful main domain
 * while avoiding absurdly long subdomains like ``xxx.yyy.zzz...provider.net``.
 */
function mainDomain(host, limit = 24) {
  if (!host) return host
  const labels = host.split('.').filter(Boolean)
  if (!labels.length) return host
  const parts = [labels.pop()]
  while (labels.length) {
    const next = labels[labels.length - 1]
    const candidate = `${next}.${parts.join('.')}`
    if (candidate.length > limit) break
    parts.unshift(labels.pop())
  }
  return parts.join('.')
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
    const trail = [v.entry, ...(v.trail || [])]
      .map((p) => stepOf(p, titles))
      .filter(Boolean)
    const utmKeys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content']
    const utmValues = utmKeys.map((k) => (v.utm || {})[k]).filter(Boolean)
    const utm = utmValues.length ? utmValues.join(' · ') : ''
    const utmTitle = Object.entries(v.utm || {})
      .map(([k, value]) => `${k}=${value}`)
      .join(', ')
    const dash = (s) => (s || '—')
    const host = v.host || ''
    const isHost = !!host
    return {
      lastSeen: formatWhen(v.start, now),
      lastSeenIso: formatWhenIso(v.start),
      lastSeenLocal: formatWhenLocal(v.start),
      langDisplay: formatLang(v.lang),
      trail,
      refererStep: stepOf(v.referer, titles),
      referer: dash(v.referer),
      ip: v.ip || '',
      ipDisplay: isHost ? mainDomain(host) : hostIP(v.ip) || v.ip || '—',
      isHost,
      lang: dash(v.lang),
      country: dash(v.country),
      city: dash(v.city),
      ua: v.ua_pretty || v.ua || '—',
      uaRaw: v.ua || '',
      utm: utm || '—',
      utmTitle,
    }
  })
}
