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
        ; (perArticle[path] || (perArticle[path] = [])).push(s)
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
  return path === '/' ? '🏠︎' : path.split('/').pop()
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
      .replace('hours', 'h')
      .replace('hour', 'h')
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
 * Compact read time for tooltips: "50s" under a minute, "1m23s" otherwise.
 */
export function formatReadTime(seconds) {
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m${seconds % 60}s`
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
 * Returns an array of [ua, count] pairs.  ``clients`` maps client hashes to
 * client records.
 */
export function countCrawlerUas(crawlers, clients) {
  const counts = {}
  for (const c of crawlers || []) {
    const client = (clients || {})[c.client] || {}
    const value = client.ua_pretty || client.ua || '(no UA)'
    counts[value] = (counts[value] || 0) + 1
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
}

/**
 * Reduce a reverse-DNS hostname to its right-most components that fit
 * within ``limit`` characters.  This keeps the meaningful main domain
 * while avoiding absurdly long subdomains like ``xxx.yyy.zzz...provider.net``.
 */
export function mainDomain(host, limit = 24) {
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
 * Group raw crawler hits by client hash and format each group as a row showing
 * every internal page that crawler visited.  Rows are sorted by most recent hit
 * first, with total hits as a tie-breaker.
 * ``clients`` maps client hashes to client records.
 */
export function formatCrawlerRows(crawlers, clients, pageTree, now = Date.now()) {
  const titles = buildTitleMap(pageTree)
  const groups = new Map()
  for (const c of crawlers || []) {
    const client = (clients || {})[c.client] || {}
    const g = groups.get(c.client) || {
      clientHash: c.client,
      client,
      lastStart: 0,
      pages: new Map(),
    }
    const start = new Date(c.start).getTime()
    if (start > g.lastStart) g.lastStart = start
    if (c.entry?.startsWith('/')) {
      const existing = g.pages.get(c.entry) || { count: 0, status: c.status || 200 }
      existing.count += 1
      if (c.status != null) existing.status = c.status
      g.pages.set(c.entry, existing)
    }
    groups.set(c.client, g)
  }
  const totalHits = (g) => {
    let n = 0
    for (const p of g.pages.values()) n += p.count
    return n
  }
  return [...groups.values()]
    .sort((a, b) => b.lastStart - a.lastStart || totalHits(b) - totalHits(a))
    .slice(0, 10)
    .map((g) => {
      const client = g.client || {}
      const host = client.host || ''
      const isHost = !!host
      return {
        lastSeen: formatWhen(g.lastStart, now),
        lastSeenIso: formatWhenIso(g.lastStart),
        lastSeenLocal: formatWhenLocal(g.lastStart),
        pages: [...g.pages.entries()]
          .sort((a, b) => b[1].count - a[1].count)
          .map(([path, info]) => ({ ...stepOf(path, titles), count: info.count, status: info.status })),
        ip: client.ip || '',
        ipDisplay: isHost ? mainDomain(host) : hostIP(client.ip) || client.ip || '—',
        isHost,
        ua: client.ua_pretty || client.ua || '—',
        uaRaw: client.ua || '',
        lang: client.lang || '—',
        langDisplay: formatLang(client.lang),
        country: client.country || '—',
        city: client.city || '—',
        total: totalHits(g),
      }
    })
}

/**
 * Group abuse hits by IP and format each group as a row with the full paths
 * probed.  Identical paths are collapsed into one entry with their hit count.
 * Flagged paths (the ones that triggered abuse classification) are lifted to
 * the top, followed by other 404s, then document GETs from the abuser.  Within
 * each category paths are sorted by count descending, then earliest first.
 * Rows are sorted by most recent hit first.  Visitor metadata comes from the
 * latest client hash seen for the IP; ``clientCount`` tells the visitor cell
 * how many distinct client variations the IP produced.  Paths are shown
 * verbatim (query string included), not resolved against the page tree.
 * ``clients`` maps client hashes to client records.
 */
export function formatAbuseRows(abuse, clients, now = Date.now()) {
  const groups = new Map()
  for (const a of abuse || []) {
    const client = (clients || {})[a.client] || {}
    const ip = client.ip || ''
    const g = groups.get(ip) || {
      ip,
      pathCounts: new Map(),
      clientHashes: new Set(),
      lastStart: 0,
      lastClient: a.client,
    }
    const start = new Date(a.start).getTime()
    if (start > g.lastStart) {
      g.lastStart = start
      g.lastClient = a.client
    }
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
    g.clientHashes.add(a.client)
    groups.set(ip, g)
  }
  const totalHits = (g) => {
    let n = 0
    for (const p of g.pathCounts.values()) n += p.count
    return n
  }
  return [...groups.values()]
    .sort((a, b) => b.lastStart - a.lastStart)
    .slice(0, 10)
    .map((g) => {
      const pathCategory = (p) => (p.flag ? 0 : p.is_404 ? 1 : 2)
      const paths = [...g.pathCounts.values()].sort(
        (a, b) =>
          pathCategory(a) - pathCategory(b) ||
          b.count - a.count ||
          a.firstStart - b.firstStart,
      )
      const client = (clients || {})[g.lastClient] || {}
      const host = client.host || ''
      const isHost = !!host
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
        clientCount: g.clientHashes.size,
        ip: client.ip || g.ip,
        ipDisplay: isHost ? mainDomain(host) : hostIP(client.ip || g.ip) || client.ip || g.ip || '—',
        isHost,
        ua: client.ua_pretty || client.ua || '—',
        uaRaw: client.ua || '',
        lang: client.lang || '—',
        langDisplay: formatLang(client.lang),
        country: client.country || '—',
        city: client.city || '—',
        total: totalHits(g),
      }
    })
}

/**
 * Format raw visit records as rows for a technical table.  Returns objects
 * with display strings; missing values become "—".  ``trail`` starts with the
 * external referer (when present), then the entry page and any further internal
 * pages or external exit origins. Only the 20 most recent visits are shown.
 * ``clients`` maps client hashes to client records.
 */
export function formatVisitRows(visits, clients, pageTree, now = Date.now()) {
  const titles = buildTitleMap(pageTree)
  return [...(visits || [])].reverse().slice(0, 20).map((v) => {
    const client = (clients || {})[v.client] || {}
    const read = v.read || {}
    const statuses = v.statuses || {}
    const trail = [v.entry, ...(v.trail || [])]
      .map((p) => {
        const step = stepOf(p, titles)
        if (step) {
          if (read[p]) step.readSeconds = read[p]
          if (statuses[p]) step.status = statuses[p]
        }
        return step
      })
      .filter(Boolean)
    const utmKeys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content']
    const utmValues = utmKeys.map((k) => (v.utm || {})[k]).filter(Boolean)
    const utm = utmValues.length ? utmValues.join(' · ') : ''
    const utmTitle = Object.entries(v.utm || {})
      .map(([k, value]) => `${k}=${value}`)
      .join(', ')
    const dash = (s) => (s || '—')
    const host = client.host || ''
    const isHost = !!host
    return {
      lastSeen: formatWhen(v.start, now),
      lastSeenIso: formatWhenIso(v.start),
      lastSeenLocal: formatWhenLocal(v.start),
      langDisplay: formatLang(client.lang),
      trail,
      refererStep: stepOf(v.referer, titles),
      referer: dash(v.referer),
      ip: client.ip || '',
      ipDisplay: isHost ? mainDomain(host) : hostIP(client.ip) || client.ip || '—',
      isHost,
      lang: dash(client.lang),
      country: dash(client.country),
      city: dash(client.city),
      ua: client.ua_pretty || client.ua || '—',
      uaRaw: client.ua || '',
      utm: utm || '—',
      utmTitle,
    }
  })
}
