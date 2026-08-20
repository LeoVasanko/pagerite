/**
 * Formatters and aggregators for summary sections: totals and the recent
 * visit trail.
 */

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
