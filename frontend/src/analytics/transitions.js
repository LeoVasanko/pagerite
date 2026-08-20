/**
 * Radial transition map and helpers.
 *
 * Radial site map: the front page at the center, each slug level on its own
 * ring. All pages of the site are shown (from /_api/pages), plus any extra
 * paths seen in transitions (deleted pages); siblings run clockwise in
 * navigation order, starting at the top. Internal path -> path transitions
 * join opposite directions into straight connections (middle width = total
 * count, wrapping the node circles at both ends); external referers/exits
 * are not shown (yet). Self-loops (reload pings) are also skipped.
 */

export const TNODE_R = 34 // node circles hold the slug and the view count

/** Flatten the site tree into navigation order via DFS. */
function buildNavigationOrder(pageTree) {
  const order = new Map()
  const walk = (items) => {
    for (const item of items || []) {
      const p = `/${item.path}`
      if (!order.has(p)) order.set(p, order.size)
      walk(item.children)
    }
  }
  walk(pageTree)
  return order
}

/** Map page paths to their article titles from the site tree. */
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

/** Extract internal page-to-page transitions, excluding self-loops. */
function collectInternalTransitions(transitions) {
  const internal = []
  for (const [fr, tos] of Object.entries(transitions || {})) {
    if (!fr.startsWith('/')) continue
    for (const [to, count] of Object.entries(tos)) {
      if (to.startsWith('/') && to !== fr) internal.push({ fr, to, count })
    }
  }
  return internal
}

/** Build nodes with depth and a path lookup map; children are wired to parents. */
function buildNodeTree(internal, navOrder) {
  const paths = new Set(['/', ...navOrder.keys()])
  for (const e of internal) { paths.add(e.fr); paths.add(e.to) }

  const depth = (p) => (p === '/' ? 0 : p.split('/').length - 1)
  const nodes = [...paths].map((p) => ({
    path: p, depth: depth(p), angle: 0, children: [],
  }))
  const byPath = new Map(nodes.map((n) => [n.path, n]))

  // Parent is the nearest ancestor present in the map, front page last.
  const parentOf = (p) => {
    let q = p
    while (q !== '/') {
      q = q.slice(0, q.lastIndexOf('/')) || '/'
      if (byPath.has(q)) return byPath.get(q)
    }
    return byPath.get('/')
  }
  for (const n of nodes) {
    if (n.path !== '/') parentOf(n.path).children.push(n)
  }

  return { nodes, byPath, root: byPath.get('/') }
}

/** Sort children by navigation order and compute each subtree's angular weight. */
function prepareWeights(root, navOrder) {
  const byNav = (a, b) =>
    (navOrder.get(a.path) ?? Infinity) - (navOrder.get(b.path) ?? Infinity)
    || a.path.localeCompare(b.path)
  const weight = (n) =>
    n.children.length ? n.children.reduce((s, k) => s + weight(k), 0) : 1 / n.depth

  const walkSort = (n) => {
    n.children.sort(byNav)
    n.children.forEach(walkSort)
  }
  walkSort(root)

  return weight
}

/** Assign angles clockwise starting from the top (-PI/2). */
function layoutAngles(root, unit, weight) {
  const lay = (n, a0) => {
    n.angle = a0
    let a = a0
    for (const k of n.children) {
      lay(k, a)
      a += weight(k) * unit
    }
  }
  let a = -Math.PI / 2
  for (const k of root.children) {
    lay(k, a)
    a += weight(k) * unit
  }
}

/** Compute radial positions, view counts and labels for each node. */
function positionNodes(nodes, maxDepth, unit, viewsData, titles) {
  // Constant radial gap between rings, equal to the arc spacing of nodes
  // along a ring: leaf arc = unit * GAP, so GAP scales up with `unit` on
  // sparse trees (where closing the circle forces wider arcs) and with
  // 1/unit on dense ones (keeping arcs at the node clearance).
  const CLEAR = 2 * TNODE_R + 12
  const GAP = CLEAR * Math.max(unit, 1 / unit)
  const radius = (d) => d * GAP

  const viewCount = (p) => {
    let n = 0
    for (const c of Object.values(viewsData?.[p] || {})) n += c
    return n
  }

  for (const n of nodes) {
    const r = radius(n.depth)
    n.x = Math.cos(n.angle) * r
    n.y = Math.sin(n.angle) * r
    n.views = viewCount(n.path)
    // Slug inside the circle; full title goes on the link title attribute.
    const slug = n.path === '/' ? '🏠' : n.path.split('/').pop()
    n.label = slug.length > 11 ? `${slug.slice(0, 10)}…` : slug
    n.title = titles.get(n.path) || ''
  }

  return { radius, GAP }
}

/**
 * Family structure at a glance: a radial spoke from each parent to its
 * first child, and a ring arc across each sibling group from first to last
 * child in navigation (clockwise) order.
 */
function buildFamilyArcs(nodes, radius) {
  const arcs = []
  for (const n of nodes) {
    if (!n.children.length) continue
    // The spoke aims along the FIRST CHILD's angle (the node's own angle
    // coincides with it, except for the center page which has none).
    const first = n.children[0]
    const r1 = radius(n.depth) + TNODE_R
    const r2 = radius(first.depth) - TNODE_R
    arcs.push({
      d: `M ${Math.cos(first.angle) * r1} ${Math.sin(first.angle) * r1} `
       + `L ${Math.cos(first.angle) * r2} ${Math.sin(first.angle) * r2}`,
    })
    if (n.children.length < 2) continue
    const r = radius(n.children[0].depth)
    const a0 = n.children[0].angle
    const a1 = n.children[n.children.length - 1].angle
    if (a1 - a0 >= 2 * Math.PI - 1e-6) continue // full circle: degenerate arc
    const large = a1 - a0 > Math.PI ? 1 : 0
    arcs.push({
      d: `M ${Math.cos(a0) * r} ${Math.sin(a0) * r} `
       + `A ${r} ${r} 0 ${large} 1 ${Math.cos(a1) * r} ${Math.sin(a1) * r}`,
    })
  }
  return arcs
}

/** Collapse opposite transition directions into one unordered pair per page pair. */
function aggregatePairs(internal) {
  const pairs = new Map() // unordered pair key -> [countAB, countBA]
  for (const e of internal) {
    const forward = e.fr < e.to
    const k = forward ? `${e.fr} ${e.to}` : `${e.to} ${e.fr}`
    const c = pairs.get(k) || [0, 0]
    c[forward ? 0 : 1] += e.count
    pairs.set(k, c)
  }
  return pairs
}

const fmtPt = (p) => `${p[0].toFixed(2)} ${p[1].toFixed(2)}`

/** Build one ribbon edge between two nodes with counts ab and ba. */
function buildRibbon(a, b, ab, ba) {
  const count = ab + ba
  const len = Math.hypot(b.x - a.x, b.y - a.y) || 1
  const ux = (b.x - a.x) / len
  const uy = (b.y - a.y) / len
  const nx = -uy
  const ny = ux

  // Half-width of the thin middle and radius of the node surround.
  const wMid = 0.75 + 6.75 * (Math.min(count, 100) / 100) ** 1.5
  const R2 = TNODE_R + 3

  // Attachment points sit somewhat forward from the side of the node,
  // leaving enough room for the surround to flow naturally into the flare.
  const BETA = (65 * Math.PI) / 180
  const END = R2 * Math.cos(BETA)
  const wEnd = R2 * Math.sin(BETA)

  // Fixed flare length, clamped so the two ends cannot overlap.
  const FLARE = Math.min(36, Math.max(0, (len - 2 * END) / 2))

  // Point on the connection centerline at distance t from A, offset s
  // perpendicular to it.
  const P = (t, s) => [
    a.x + t * ux + s * nx,
    a.y + t * uy + s * ny,
  ]

  // Arc around a node from p to q the long way, passing its back side.
  const wrap = (p, q, node, back) => {
    const ang = (pt2) =>
      Math.atan2(pt2[1] - node[1], pt2[0] - node[0])

    const TAU = 2 * Math.PI
    const da = ((ang(back) - ang(p)) % TAU + TAU) % TAU
    const db = ((ang(q) - ang(p)) % TAU + TAU) % TAU

    return `A ${R2} ${R2} 0 1 ${da < db ? 1 : 0} ${fmtPt(q)} `
  }

  // Build one side of a flare in node -> middle order.
  const flarePoints = (endT, midT, s, dir) => {
    const span = Math.abs(midT - endT)
    const pEnd = P(endT, s * wEnd)
    const pMid = P(midT, s * wMid)

    // At the node, leave tangent to the circular surround.
    // The circle radius at the attachment is locally:
    //   A: (+END, ±wEnd)
    //   B: (-END, ±wEnd)
    // A perpendicular tangent pointing into the connection therefore has
    // these centerline/normal components.
    const tangentT = dir * wEnd / R2
    const tangentS = -s * END / R2

    const hEnd = span * 0.65
    const hMid = span * 0.4

    const cEnd = P(
      endT + tangentT * hEnd,
      s * wEnd + tangentS * hEnd,
    )

    // At the thin end, arrive parallel with the centerline.
    const cMid = P(
      midT - dir * hMid,
      s * wMid,
    )

    return { pEnd, cEnd, cMid, pMid }
  }

  // Emit a cubic in either traversal direction. Reversing a cubic requires
  // swapping its control points, rather than recalculating the geometry.
  const curve = (f, reverse = false) => {
    if (!reverse) {
      return `C ${fmtPt(f.cEnd)} ${fmtPt(f.cMid)} ${fmtPt(f.pMid)} `
    }
    return `C ${fmtPt(f.cMid)} ${fmtPt(f.cEnd)} ${fmtPt(f.pEnd)} `
  }

  const LA = P(END, wEnd)
  const RA = P(END, -wEnd)
  const LB = P(len - END, wEnd)
  const RB = P(len - END, -wEnd)

  const aLeft = flarePoints(END, END + FLARE, 1, 1)
  const bLeft = flarePoints(len - END, len - END - FLARE, 1, -1)
  const bRight = flarePoints(len - END, len - END - FLARE, -1, -1)
  const aRight = flarePoints(END, END + FLARE, -1, 1)

  const d = `M ${fmtPt(LA)} `
    + curve(aLeft)
    + `L ${fmtPt(bLeft.pMid)} `
    + curve(bLeft, true)
    + wrap(LB, RB, [b.x, b.y], P(len + R2, 0))
    + curve(bRight)
    + `L ${fmtPt(aRight.pMid)} `
    + curve(aRight, true)
    + wrap(RA, LA, [a.x, a.y], P(-R2, 0))
    + 'Z'

  return {
    d,
    title: `${a.path} ↔ ${b.path}: ${count} (${ab} / ${ba})`,
  }
}

/** Build ribbon edges for every aggregated page-to-page pair. */
function buildRibbonEdges(pairs, byPath) {
  return [...pairs].map(([k, [ab, ba]]) => {
    const [pf, pt] = k.split(' ')
    const a = byPath.get(pf)
    const b = byPath.get(pt)
    return buildRibbon(a, b, ab, ba)
  })
}

/** Parse a visit start timestamp, which may already be numeric or an ISO string. */
function visitStart(v) {
  return typeof v.start === 'number' ? v.start : Date.parse(v.start)
}

/**
 * Derive internal path -> path transitions from the visits list, optionally
 * restricted to a time window. This is the only time-filterable source of
 * transitions (the server-side aggregate has no per-transition timestamps).
 * Re-visits within the same visit are not recorded in `trail`, so this yields
 * first-seen navigation chains rather than every ping.
 */
export function buildTransitionsFromVisits(visits, t0, t1) {
  const transitions = {}
  for (const v of visits || []) {
    const start = visitStart(v)
    if ((t0 != null && start < t0) || (t1 != null && start >= t1)) continue
    const path = [v.entry, ...(v.trail || [])].filter((p) => p?.startsWith('/'))
    for (let i = 0; i < path.length - 1; i++) {
      const fr = path[i]
      const to = path[i + 1]
      if (fr === to) continue
      transitions[fr] = transitions[fr] || {}
      transitions[fr][to] = (transitions[fr][to] || 0) + 1
    }
  }
  return transitions
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

/**
 * Build the radial transition map model.
 * Returns { nodes, edges, arcs, r } or null when there is nothing to show.
 */
export function buildTransitionGraph(data, pageTree) {
  const internal = collectInternalTransitions(data?.transitions)
  const navOrder = buildNavigationOrder(pageTree)
  const titles = buildTitleMap(pageTree)

  if (!internal.length && !navOrder.size) return null

  const { nodes, byPath, root } = buildNodeTree(internal, navOrder)
  const weightFn = prepareWeights(root, navOrder)
  const unit = (2 * Math.PI) / weightFn(root)
  layoutAngles(root, unit, weightFn)

  const maxDepth = Math.max(1, ...nodes.map((n) => n.depth))
  const { radius, GAP } = positionNodes(nodes, maxDepth, unit, data?.views, titles)
  const arcs = buildFamilyArcs(nodes, radius)
  const pairs = aggregatePairs(internal)
  const edges = buildRibbonEdges(pairs, byPath)

  // Tight bounding box of the actual nodes; edges and arcs stay within the
  // node circles, so node bounds plus node radius are sufficient.
  const pad = 16
  const xs = nodes.map((n) => n.x)
  const ys = nodes.map((n) => n.y)
  const bounds = {
    x0: Math.min(...xs) - TNODE_R - pad,
    y0: Math.min(...ys) - TNODE_R - pad,
    x1: Math.max(...xs) + TNODE_R + pad,
    y1: Math.max(...ys) + TNODE_R + pad,
  }

  return {
    nodes,
    edges,
    arcs,
    bounds,
  }
}
