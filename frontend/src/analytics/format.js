/**
 * Formatters and aggregators for summary sections: totals and the recent
 * visit trail.
 */
import { flagFor, langName } from '../langs.js'

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

function showCopiedFeedback(el, event) {
  if (typeof document === 'undefined') return
  const popup = document.createElement('span')
  popup.textContent = 'Copied!'
  popup.className = 'copy-popup'
  // Fixed to the viewport at the click point: table cells clip absolute
  // popups with their overflow: hidden ellipsis styling.
  const x = event?.clientX ?? 0
  const y = event?.clientY ?? 0
  popup.style.cssText =
    `position:fixed;left:${x}px;top:${y}px;` +
    'transform:translate(-50%, calc(-100% - 0.5rem));padding:0.15rem 0.4rem;' +
    'background:var(--text, CanvasText);color:var(--bg, Canvas);' +
    'border-radius:0.25rem;font-size:0.75rem;white-space:nowrap;' +
    'pointer-events:none;z-index:100;'
  document.body.appendChild(popup)
  setTimeout(() => popup.remove(), 1200)
}

/** Copy the full IP to the clipboard and show a brief "Copied!" popup. */
export async function copyIp(ip, event) {
  if (!ip) return
  try {
    await navigator.clipboard.writeText(ip)
    showCopiedFeedback(event?.currentTarget, event)
  } catch {
    /* ignore */
  }
}

/** Copy arbitrary text to the clipboard and show a brief "Copied!" popup. */
export async function copyList(text, event) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    showCopiedFeedback(event?.currentTarget, event)
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

/** path -> accumulated read seconds for a visit, derived from its trail. */
export function readMapOf(v) {
  const map = {}
  for (const item of Object.values(v.trail || {})) {
    if (item.read) map[item.to] = (map[item.to] || 0) + item.read
  }
  return map
}

/** Average minutes per visit and average of per-article median read minutes. */
export function calcReadStats(visits) {
  const perArticle = {}
  let totalVisitSeconds = 0
  let visitCount = 0
  for (const v of visits || []) {
    const read = readMapOf(v)
    const secs = Object.values(read).filter((s) => s >= MIN_READ_SECONDS)
    if (!secs.length) continue
    visitCount++
    totalVisitSeconds += secs.reduce((a, b) => a + b, 0)
    for (const [path, s] of Object.entries(read)) {
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

/** Origin (scheme://host) of an external https URL, for favicon lookup. */
function externalOrigin(url) {
  try {
    return new URL(url).origin
  } catch {
    return ''
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
      origin: externalOrigin(path),
    }
  }
  return null
}

/**
 * Badge data combining a visit's/crawler's external referer origin with the
 * visit's UTM tags: the origin as the badge link/label (the favicon is
 * looked up by origin in the component), a compact UTM summary (the few
 * most informative values) as ``utm`` with the full ``utm_*=value`` list
 * as ``utmCopy`` for click-to-copy, and a one-fact-per-line tooltip — the
 * full origin URL on the first line, then every ``utm_*=value`` pair.
 * Null when there is no external referer and no UTM tag (a plain direct
 * visit).
 */
function refererBadgeOf(referer, titles, utmTags = {}) {
  const step = stepOf(referer, titles)
  const external = step?.external ? step : null
  // Compact UTM summary, in display order source, campaign, content, term:
  // source only when no referer is known (it just repeats where the visitor
  // came from), content only as a stand-in when there is no term.  The
  // remaining tags (medium and any nonstandard utm_*) fill in only when
  // fewer than three of these more useful items exist — and never when a
  // term is present (the term alone says enough).  The tooltip keeps
  // every tag, one pair per line.
  const useful = []
  if (utmTags.utm_source && !referer) useful.push(utmTags.utm_source)
  if (utmTags.utm_campaign) useful.push(utmTags.utm_campaign)
  if (utmTags.utm_content && !utmTags.utm_term) useful.push(utmTags.utm_content)
  if (utmTags.utm_term) useful.push(utmTags.utm_term)
  const rest = useful.length < 3 && !utmTags.utm_term
    ? Object.keys(utmTags)
        .filter((k) => !['utm_source', 'utm_campaign', 'utm_content', 'utm_term'].includes(k))
        .map((k) => utmTags[k])
        .filter(Boolean)
    : []
  const utm = [...useful, ...rest].join(' · ')
  if (!external && !utm) return null
  const utmCopy = Object.entries(utmTags)
    .map(([k, value]) => `${k}=${value}`)
    .join('\n')
  return {
    href: external?.origin || '',
    label: external?.slug || '',
    origin: external?.origin || '',
    utm,
    utmCopy,
    title: [
      ...(external ? [external.origin] : []),
      ...Object.entries(utmTags).map(([k, value]) => `${k}=${value}`),
    ].join('\n'),
  }
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
      steps: [v.referer, ...Object.values(v.trail || {}).map((t) => t.to)]
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
    const value = client.uarite?.pretty || client.ua || '(no UA)'
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
 * first, with total hits as a tie-breaker.  The group's ``refererBadge`` is
 * the latest external referer seen for the crawler — spiders often advertise
 * their own site there — rendered as a badge with its favicon like visit
 * referers.
 * ``clients`` maps client hashes to client records.
 */
export function formatCrawlerRows(crawlers, clients, pageTree, now = Date.now(), site = { multilingual: false, primaryLang: '' }) {
  const titles = buildTitleMap(pageTree)
  const groups = new Map()
  for (const c of crawlers || []) {
    const client = (clients || {})[c.client] || {}
    const g = groups.get(c.client) || {
      clientHash: c.client,
      client,
      lastStart: 0,
      referer: '',
      pages: new Map(),
      langs: new Set(),
    }
    const start = new Date(c.start).getTime()
    if (start > g.lastStart) g.lastStart = start
    if (c.referer) g.referer = c.referer
    if (c.lang) g.langs.add(c.lang)
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
      // Rendered languages read, shown only when they say something the
      // primary language alone would not (multilingual sites only).
      const langs = [...g.langs].sort()
      const showLangs =
        site.multilingual && (langs.length > 1 || (langs[0] && langs[0] !== site.primaryLang))
      return {
        lastSeen: formatWhen(g.lastStart, now),
        lastSeenIso: formatWhenIso(g.lastStart),
        lastSeenLocal: formatWhenLocal(g.lastStart),
        refererBadge: refererBadgeOf(g.referer, titles),
        pages: [...g.pages.entries()]
          .sort((a, b) => b[1].count - a[1].count)
          .map(([path, info]) => ({ ...stepOf(path, titles), count: info.count, status: info.status })),
        readFlags: showLangs
          ? langs.map((l) => ({ flag: flagFor(l), name: langName(l) })).filter((f) => f.flag)
          : [],
        ip: client.ip || '',
        ipDisplay: isHost ? mainDomain(host) : hostIP(client.ip) || client.ip || '—',
        isHost,
        ua: client.uarite?.pretty || client.ua || '—',
        uaRaw: client.ua || '',
        uaUrl: client.uarite?.url || '',
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
 * probed.  Identical requests (same path and status class) are collapsed
 * into one entry with their hit count; a path's 404 probes and its real
 * (200) reads never merge.
 * The paths split into two lists: ``paths`` holds the 404 probes (flagged
 * paths — the ones that triggered abuse classification — first, then other
 * 404s) shown verbatim, query string included, and ``articles`` holds the
 * real (200) document GETs as trail steps resolved against the page tree
 * (query string stripped), rendered like the visitor/crawler trails.  Within
 * each list paths are sorted by count descending, then earliest first.
 * Rows are sorted by most recent hit first.  Visitor metadata comes from the
 * latest client hash seen for the IP; ``clientCount`` tells the visitor cell
 * how many distinct client variations the IP produced.
 * ``clients`` maps client hashes to client records.
 */
export function formatAbuseRows(abuse, clients, pageTree, now = Date.now()) {
  const titles = buildTitleMap(pageTree)
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
    // Collapse identical requests, but never merge a path's 404 probes with
    // its real (200) reads — a page probed while missing and later created
    // must show up in both columns, not flip to "articles read".
    const key = `${a.is_404 ? '4' : '2'}${path}`
    const existing = g.pathCounts.get(key) || {
      path,
      count: 0,
      firstStart: start,
      flag: a.flag || false,
      is_404: a.is_404 || false,
    }
    existing.count += 1
    if (start < existing.firstStart) existing.firstStart = start
    if (a.flag) existing.flag = true
    g.pathCounts.set(key, existing)
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
      const all = [...g.pathCounts.values()]
      const byCount = (a, b) => b.count - a.count || a.firstStart - b.firstStart
      const paths = all
        .filter((p) => p.flag || p.is_404)
        .sort((a, b) => (a.flag ? 0 : 1) - (b.flag ? 0 : 1) || byCount(a, b))
      const articles = all.filter((p) => !p.flag && !p.is_404).sort(byCount)
      const pathList = (list) =>
        list.map((p) => (p.count > 1 ? `${p.count}× ${p.path}` : p.path)).join('\n')
      const client = (clients || {})[g.lastClient] || {}
      const host = client.host || ''
      const isHost = !!host
      const uaRaws = [
        ...new Set(
          [...g.clientHashes]
            .map((h) => (clients || {})[h]?.ua)
            .filter(Boolean),
        ),
      ].join('\n')
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
        allPaths: pathList(paths),
        articles: articles
          .map((p) => {
            const step = stepOf(p.path.split('?')[0], titles)
            return step ? { ...step, count: p.count } : null
          })
          .filter(Boolean),
        allArticles: pathList(articles),
        clientCount: g.clientHashes.size,
        ip: client.ip || g.ip,
        ipDisplay: isHost ? mainDomain(host) : hostIP(client.ip || g.ip) || client.ip || g.ip || '—',
        isHost,
        ua: client.uarite?.pretty || client.ua || '—',
        uaRaw: client.ua || '',
        uaUrl: client.uarite?.url || '',
        uaRaws,
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
 * with display strings; missing values become "—".  The external referer
 * (when present) and the UTM tags ride along as ``refererBadge``; ``trail``
 * holds the entry page and any further internal pages or external exit
 * origins; consecutive views of the same page (e.g. a
 * language switch re-view) merge into one step that keeps the
 * consecutive-distinct rendered languages, summed read time, and the latest
 * status.  On multilingual sites the rendered languages surface as flag
 * icons: a visit read entirely in one non-primary language gets ``rowFlag``,
 * and a visit spanning languages gets per-step ``langFlags`` markers where
 * the language begins or changes.  Only the 20 most recent visits are shown.
 * ``clients`` maps client hashes to client records; ``site`` carries the
 * payload's multilingual/primary-language context.
 */
export function formatVisitRows(visits, clients, pageTree, now = Date.now(), site = { multilingual: false, primaryLang: '' }) {
  const titles = buildTitleMap(pageTree)
  return [...(visits || [])].reverse().slice(0, 20).map((v) => {
    const client = (clients || {})[v.client] || {}
    const steps = Object.values(v.trail || {})
      .map((item) => {
        const step = stepOf(item.to, titles)
        if (step) {
          if (item.read) step.readSeconds = item.read
          if (item.status) step.status = item.status
          if (item.lang) step.lang = item.lang
        }
        return step
      })
      .filter(Boolean)
    const trail = []
    for (const step of steps) {
      const prev = trail[trail.length - 1]
      if (prev && prev.path === step.path) {
        if (step.lang && step.lang !== prev.langs[prev.langs.length - 1]) prev.langs.push(step.lang)
        if (step.readSeconds) prev.readSeconds = (prev.readSeconds || 0) + step.readSeconds
        if (step.status) prev.status = step.status
      } else {
        step.langs = step.lang ? [step.lang] : []
        trail.push(step)
      }
    }
    const distinctLangs = new Set(trail.flatMap((s) => s.langs))
    const dash = (s) => (s || '—')
    const host = client.host || ''
    const isHost = !!host
    const row = {
      lastSeen: formatWhen(v.start, now),
      lastSeenIso: formatWhenIso(v.start),
      lastSeenLocal: formatWhenLocal(v.start),
      langDisplay: formatLang(client.lang),
      trail,
      refererBadge: refererBadgeOf(v.referer, titles, v.utm),
      ip: client.ip || '',
      ipDisplay: isHost ? mainDomain(host) : hostIP(client.ip) || client.ip || '—',
      isHost,
      lang: dash(client.lang),
      country: dash(client.country),
      city: dash(client.city),
      ua: client.uarite?.pretty || client.ua || '—',
      uaRaw: client.ua || '',
      uaUrl: client.uarite?.url || '',
    }
    if (site.multilingual && distinctLangs.size) {
      if (distinctLangs.size === 1) {
        const [tag] = distinctLangs
        const flag = flagFor(tag)
        if (flag && tag !== site.primaryLang) {
          row.rowFlag = flag
          row.rowFlagTitle = langName(tag)
        }
      } else {
        // Flag the steps where the rendered language begins or changes;
        // lang-less steps keep the comparison chain going, they never flag.
        let lastLang = null
        for (const step of trail) {
          if (!step.langs.length) continue
          if (!lastLang || step.langs[step.langs.length - 1] !== lastLang) {
            step.langFlags = step.langs.map(flagFor).filter(Boolean)
          }
          lastLang = step.langs[step.langs.length - 1]
        }
      }
    }
    return row
  })
}
