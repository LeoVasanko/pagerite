/**
 * Radial transition map and helpers.
 *
 * Radial site map: the front page at the center, each slug level on its own
 * ring. All pages of the site are shown (from /_api/pages), plus any extra
 * paths seen in transitions (deleted pages); siblings run clockwise in
 * navigation order, starting at the top. Internal path -> path transitions
 * join opposite directions into straight connections (middle width = total
 * count; connectors flare into the node pills at both ends and wrap
 * around their backs, surrounding them; the pills are drawn on top). Connection width grows
 * logarithmically with the count (a single count renders as a ~1 px
 * line, uncapped growth); connections carrying less than 1% of the total
 * traffic are pruned, which naturally keeps the graph under ~100
 * connections. Animated beads flow along every edge in each direction,
 * emitted at time intervals inversely proportional (linear) to the
 * directional count.
 * External sources appear as nodes in a row above the map. Sources are
 * identified from visit records in this order: utm_campaign, utm_source,
 * referer, then other utm_* tags. Visits with a UTM tag are grouped under
 * that tag's value, not under the referer domain. A UTM source node only
 * becomes a clickable link when every visit carrying that UTM tag came
 * from the same referer. External exits are full-size nodes just outside
 * their source page, angled away from the center. Each distinct full exit
 * URL is its own node. Self-loops (reload pings) are skipped.
 */

import { MIN_READ_SECONDS } from './format.js'

// Nodes are constant-size pills (stadium rects) holding the slug and the
// view count on two centered lines. TNODE_BOUND is the pill's bounding
// radius, used for layout clearance and placement; connectors and flows
// use the exact outline geometry instead (pillContact below).
export const TNODE_W = 160
export const TNODE_H = 54
const TNODE_BOUND = Math.hypot(TNODE_W, TNODE_H) / 2

const PILL_R = TNODE_H / 2 // cap radius and straight-section half-height
const PILL_OFF = TNODE_W / 2 - PILL_R // x offset of the cap centers

/**
 * Where the ray from a node center along (ux, uy) exits the pill outline
 * (a capsule: straight top/bottom plus semicircular caps), enlarged by
 * `margin`. Returns the distance `t` to the contact point and the outline
 * arc position `s` of that point (see pillPointAt).
 */
const pillContact = (ux, uy, margin = 0) => {
  const r = PILL_R + margin
  const off = PILL_OFF + margin
  const q = (Math.PI / 2) * r
  // Straight top/bottom: valid when the crossing lands on the flat section.
  let tf = Infinity
  if (Math.abs(uy) > 1e-9) {
    const t = r / Math.abs(uy)
    if (Math.abs(t * ux) <= off + 1e-9) tf = t
  }
  // Rounded cap on the side the ray points to.
  const cx = off * (ux >= 0 ? 1 : -1)
  const disc = r * r - (cx * uy) ** 2
  const tc = disc >= 0 ? cx * ux + Math.sqrt(disc) : Infinity
  if (tf <= tc) {
    const x = tf * ux
    return { t: tf, s: uy > 0 ? q + off - x : q + 2 * off + Math.PI * r + x + off }
  }
  if (tc < Infinity) {
    let th = Math.atan2(tc * uy, tc * ux - cx)
    if (th < 0) th += 2 * Math.PI
    const s = cx > 0
      ? th <= Math.PI / 2
        ? th * r
        : q + 4 * off + Math.PI * r + (th - (3 * Math.PI) / 2) * r
      : q + 2 * off + (th - Math.PI / 2) * r
    return { t: tc, s }
  }
  return { t: TNODE_BOUND + margin, s: 0 }
}

/** Total perimeter of the (margined) pill outline. */
const pillPerimeter = (margin = 0) =>
  4 * (PILL_OFF + margin) + 2 * Math.PI * (PILL_R + margin)

/**
 * Point on the pill outline at arc position `s`, counterclockwise from the
 * right cap tip: right cap up, top flat right-to-left, left cap down,
 * bottom flat left-to-right, right cap up to the tip. Pills are never
 * rotated, so the returned offset from the node center is in absolute
 * coordinates.
 */
const pillPointAt = (s, margin = 0) => {
  const r = PILL_R + margin
  const off = PILL_OFF + margin
  const P = pillPerimeter(margin)
  const q = (Math.PI / 2) * r
  s = ((s % P) + P) % P
  if (s < q) {
    const th = s / r
    return [off + r * Math.cos(th), r * Math.sin(th)]
  }
  s -= q
  if (s < 2 * off) return [off - s, r]
  s -= 2 * off
  if (s < Math.PI * r) {
    const th = Math.PI / 2 + s / r
    return [-off + r * Math.cos(th), r * Math.sin(th)]
  }
  s -= Math.PI * r
  if (s < 2 * off) return [-off + s, -r]
  s -= 2 * off
  const th = (3 * Math.PI) / 2 + s / r
  return [off + r * Math.cos(th), r * Math.sin(th)]
}

/** Unit tangent to the pill outline at arc position `s`, in the direction
 * of increasing `s` (numeric; exact on both flats and caps). */
const pillTangent = (s, margin = 0) => {
  const [x1, y1] = pillPointAt(s - 0.5, margin)
  const [x2, y2] = pillPointAt(s + 0.5, margin)
  const m = Math.hypot(x2 - x1, y2 - y1) || 1
  return [(x2 - x1) / m, (y2 - y1) / m]
}

// Edge width (half-width of the thin middle) grows logarithmically with
// the count. The constants are scaled down by ~10× so busy ranges (day,
// year) do not overwhelm the graph with fat connectors. A single recorded
// transition still renders as a faint ~0.4 px line. Connections carrying
// less than PRUNE_FRACTION of the total traffic are not drawn at all (this
// also keeps the graph under ~100 connections).
const WMID_MIN = 0.2
const WIDTH_GROWTH = 0.15
const PRUNE_FRACTION = 0.01

// Beads: each edge direction emits beads at count * BEAD_RATE beads per
// second (linear in the count). The rate is reduced ~10× across all time
// scales to keep the animation lightweight. The component simulates every
// bead independently in JS at BEAD_SPEED along the edge, with no limit on
// beads in flight.
export const BEAD_SPEED = 180 // svg units per second
export const BEAD_R = 3.2
const BEAD_RATE = 0.012 // beads per second per recorded transition
const FLOW_OFFSET = 3 // lane offset to the right of the travel direction

const MAX_EXT_IN = 8 // referer nodes in the top row
const MAX_EXT_OUT = 12 // exit nodes, at most MAX_EXT_OUT_PER_PAGE per page
const MAX_EXT_OUT_PER_PAGE = 3

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

/** Domain-only label for an external origin (path and www. removed). */
function extLabel(ext) {
  try {
    const host = new URL(ext).hostname.replace(/^www\./, '')
    return host.length > 25 ? `${host.slice(0, 24)}…` : host
  } catch {
    const s = ext.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0]
    return s.length > 25 ? `${s.slice(0, 24)}…` : s
  }
}

/**
 * Collect outgoing external transitions: page path -> full exit URL.
 * Aggregated per (URL, page) pair. Incoming external links are now derived
 * from visit records (which carry UTM tags), so only exits remain here.
 */
function collectExitPairs(transitions) {
  const pairs = new Map() // `${ext} ${page}` -> {ext, page, out}
  for (const [fr, tos] of Object.entries(transitions || {})) {
    if (!fr.startsWith('/')) continue // ignore external -> anything
    for (const [to, count] of Object.entries(tos)) {
      if (!to.startsWith('http')) continue
      const k = `${to} ${fr}`
      const p = pairs.get(k) || { ext: to, page: fr, out: 0 }
      p.out += count
      pairs.set(k, p)
    }
  }
  return [...pairs.values()]
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

/** Compute median reading time per article in whole minutes. */
function buildReadMinutes(visits) {
  const times = {}
  for (const v of visits || []) {
    for (const [path, sec] of Object.entries(v.read || {})) {
      if (sec >= MIN_READ_SECONDS) {
        ;(times[path] || (times[path] = [])).push(sec)
      }
    }
  }
  const minutes = {}
  for (const [path, arr] of Object.entries(times)) {
    arr.sort((a, b) => a - b)
    const mid = Math.floor(arr.length / 2)
    const median =
      arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2
    minutes[path] = Math.max(1, Math.round(median / 60))
  }
  return minutes
}

/** Compute radial positions, view counts and labels for each node. */
function positionNodes(nodes, maxDepth, unit, viewsData, titles, readMinutes) {
  // Constant radial gap between rings, equal to the arc spacing of nodes
  // along a ring: leaf arc = unit * GAP, so GAP scales up with `unit` on
  // sparse trees (where closing the circle forces wider arcs) and with
  // 1/unit on dense ones (keeping arcs at the node clearance).
  // Clearance is pill-width based.
  const CLEAR = TNODE_W + 20
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
    n.readMin = readMinutes[n.path] || 0
    // Slug inside the pill; full title goes on the link title attribute.
    const slug = n.path === '/' ? '🏠︎' : n.path.split('/').pop()
    n.label = slug.length > 16 ? `${slug.slice(0, 15)}…` : slug
    n.title = titles.get(n.path) || ''
    // Category (non-leaf) pages with no views in this window are left
    // blank to keep the layout, but their pill/label is not drawn.
    n.hidden = n.children.length > 0 && n.views === 0
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
    const pillR = pillContact(Math.cos(first.angle), Math.sin(first.angle)).t
    const r1 = radius(n.depth) + pillR
    const r2 = radius(first.depth) - pillR
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
      r,
      a0,
      a1,
    })
  }
  return arcs
}

/** Bounding box of a circular arc centred at the origin, sampled. */
function arcBounds(r, a0, a1) {
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  const steps = 36
  for (let i = 0; i <= steps; i++) {
    const t = a0 + (a1 - a0) * (i / steps)
    const x = Math.cos(t) * r
    const y = Math.sin(t) * r
    if (x < x0) x0 = x
    if (y < y0) y0 = y
    if (x > x1) x1 = x
    if (y > y1) y1 = y
  }
  return { x0, y0, x1, y1 }
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

/**
 * Build one ribbon edge between two nodes with counts ab and ba.
 * `wMid` is the half-width of the thin middle (already strength-scaled by
 * the caller). Each end flares into the node's pill surround (the outline
 * enlarged by margin S): the flare contact points follow the pill outline
 * a constant arc distance to each side of the direct contact point, and
 * the back of the ribbon wraps all the way around the pill between them,
 * surrounding the node. The pills themselves are drawn on top.
 */
function buildRibbon(a, b, ab, ba, wMid, external = false) {
  const count = ab + ba
  const len = Math.hypot(b.x - a.x, b.y - a.y) || 1
  const ux = (b.x - a.x) / len
  const uy = (b.y - a.y) / len
  const nx = -uy
  const ny = ux

  // Direct contact: where the centerline exits each pill's surround.
  const S = 4
  const cA = pillContact(ux, uy, S)
  const cB = pillContact(-ux, -uy, S)

  // Flares take a fair share of the free span while leaving the
  // count-scaled thin middle a visible share of the connection length.
  // The maximum flare length scales with the contact distance so wide
  // approach angles still show a wide connector end.
  const free = Math.max(0, len - cA.t - cB.t)
  const FLARE = Math.min(Math.max(cA.t, cB.t) * 1.2, free * 0.4)

  // Flare endpoints: walk the outline a constant arc distance to each
  // side of the direct contact point (spanning flats and caps alike).
  const D = (Math.PI / 4) * (PILL_R + S)
  // Per node: endpoints for the +n (left) and -n (right) flare sides,
  // each with its arc position, absolute point, and an outline tangent
  // oriented back toward the direct contact point.
  const ends = (cx, cy, contact) => {
    const pick = (s) => {
      const [px, py] = pillPointAt(s, S)
      // Outline tangent oriented back toward the direct contact point
      // (the flare side sweeps from the contact point around to its
      // endpoint and into the connection), so it can never fork outward.
      const tan = pillTangent(s, S)
      if (s > contact.s) { tan[0] = -tan[0]; tan[1] = -tan[1] }
      return { s, p: [cx + px, cy + py], tan, side: px * nx + py * ny }
    }
    const plus = pick(contact.s + D)
    const minus = pick(contact.s - D)
    return plus.side >= 0 ? [plus, minus] : [minus, plus]
  }
  const [aLeftEnd, aRightEnd] = ends(a.x, a.y, cA)
  const [bLeftEnd, bRightEnd] = ends(b.x, b.y, cB)

  // Point on the connection centerline at distance t from A, offset s
  // perpendicular to it.
  const P = (t, s) => [
    a.x + t * ux + s * nx,
    a.y + t * uy + s * ny,
  ]

  // One side of a flare: from the outline endpoint, leaving tangent to
  // the pill outline, to the connection middle arriving parallel with
  // the centerline. The tangent pull is clamped so the control point
  // stays well on its own side of the centerline — otherwise a long
  // flare on a rounded cap crosses the opposite side.
  const flarePoints = (end, midT, s, dir) => {
    let hEnd = FLARE * 0.65
    const hMid = FLARE * 0.4
    const tanS = end.tan[0] * nx + end.tan[1] * ny // inward rate
    if (tanS * end.side < 0) {
      hEnd = Math.min(hEnd, (Math.abs(end.side) * 0.6) / Math.abs(tanS))
    }
    return {
      pEnd: end.p,
      cEnd: [end.p[0] + end.tan[0] * hEnd, end.p[1] + end.tan[1] * hEnd],
      cMid: P(midT - dir * hMid, s * wMid),
      pMid: P(midT, s * wMid),
    }
  }

  // Emit a cubic in either traversal direction. Reversing a cubic requires
  // swapping its control points, rather than recalculating the geometry.
  const curve = (f, reverse = false) => {
    if (!reverse) {
      return `C ${fmtPt(f.cEnd)} ${fmtPt(f.cMid)} ${fmtPt(f.pMid)} `
    }
    return `C ${fmtPt(f.cMid)} ${fmtPt(f.cEnd)} ${fmtPt(f.pEnd)} `
  }

  // Trace the surround outline the long way around (behind the node) from
  // arc s1 to arc s2. Sampled as a polyline: the visible result is a thin
  // halo hugging the pill, so exact arc segments are unnecessary.
  const outlineWrap = (cx, cy, s1, s2) => {
    const per = pillPerimeter(S)
    const dPlus = ((s2 - s1) % per + per) % per
    const total = dPlus > per / 2 ? dPlus : per - dPlus
    const dir = dPlus > per / 2 ? 1 : -1
    const n = Math.max(4, Math.ceil(total / 6))
    let out = ''
    for (let i = 1; i <= n; i++) {
      const [x, y] = pillPointAt(s1 + (dir * total * i) / n, S)
      out += `L ${(cx + x).toFixed(2)} ${(cy + y).toFixed(2)} `
    }
    return out
  }

  const aLeft = flarePoints(aLeftEnd, cA.t + FLARE, 1, 1)
  const bLeft = flarePoints(bLeftEnd, len - cB.t - FLARE, 1, -1)
  const bRight = flarePoints(bRightEnd, len - cB.t - FLARE, -1, -1)
  const aRight = flarePoints(aRightEnd, cA.t + FLARE, -1, 1)

  // Each end wraps the full back of the node pill between its two flare
  // contact points (bLeft -> bRight around B, aRight -> aLeft around A).
  const d = `M ${fmtPt(aLeft.pEnd)} `
    + curve(aLeft)
    + `L ${fmtPt(bLeft.pMid)} `
    + curve(bLeft, true)
    + outlineWrap(b.x, b.y, bLeftEnd.s, bRightEnd.s)
    + curve(bRight)
    + `L ${fmtPt(aRight.pMid)} `
    + curve(aRight, true)
    + outlineWrap(a.x, a.y, aRightEnd.s, aLeftEnd.s)
    + 'Z'

  return {
    d,
    title: `${a.path} ↔ ${b.path}: ${count} (${ab} / ${ba})`,
    external,
  }
}

/**
 * Flow descriptors for the bead animation, one per edge direction with a
 * nonzero count: a straight segment running from inside the source node
 * to inside the target node (beads render under the node pills, so
 * they emerge from and vanish beneath the nodes rather than popping in
 * at the surround), plus the emission interval (seconds between beads,
 * inverse of count * BEAD_RATE). Each segment is offset to the
 * right-hand side of its travel direction, so opposing flows on the same
 * edge run on parallel lanes instead of colliding. The component turns
 * these into independently simulated beads.
 */
function buildFlows(a, b, ab, ba, visualScale = 1) {
  const len = Math.hypot(b.x - a.x, b.y - a.y) || 1
  const ux = (b.x - a.x) / len
  const uy = (b.y - a.y) / len
  const rA = pillContact(ux, uy).t
  const rB = pillContact(-ux, -uy).t
  const t0 = rA / 3
  const t1 = len - rB / 3
  if (t1 - t0 < 12) return []

  // Unit normal pointing to the visual right of the A -> B direction.
  const rx = -uy
  const ry = ux
  const span = t1 - t0
  const flow = (count, fromT, toT) => {
    // Each direction shifts to its own right, away from the opposing lane.
    const s = fromT < toT ? FLOW_OFFSET : -FLOW_OFFSET
    return {
      x1: a.x + fromT * ux + s * rx,
      y1: a.y + fromT * uy + s * ry,
      x2: a.x + toT * ux + s * rx,
      y2: a.y + toT * uy + s * ry,
      len: span,
      interval: 1 / (count * BEAD_RATE * visualScale),
    }
  }
  const flows = []
  if (ab) flows.push(flow(ab, t0, t1))
  if (ba) flows.push(flow(ba, t1, t0))
  return flows
}

/**
 * Half-width for a connection middle: logarithmic in the count, anchored
 * so a single count lands exactly at WMID_MIN (~1 px line), uncapped.
 * Absolute on purpose — cool routes stay visible regardless of how hot
 * the hottest connection is.
 */
const scaledWidth = (count) => {
  if (count <= 0) return 0
  return WMID_MIN + WIDTH_GROWTH * Math.log1p(count - 1)
}

/**
 * Build ribbon edges and bead flows for every aggregated page-to-page
 * pair. Pairs carrying less than PRUNE_FRACTION of the total internal
 * traffic are pruned (this naturally bounds the graph to ~100 edges).
 */
function buildInternalEdges(pairs, byPath, visualScale = 1) {
  let total = 0
  for (const [, [ab, ba]] of pairs) total += ab + ba
  const minCount = total * PRUNE_FRACTION

  const edges = []
  const flows = []
  for (const [k, [ab, ba]] of pairs) {
    if (ab + ba < minCount) continue
    const [pf, pt] = k.split(' ')
    const a = byPath.get(pf)
    const b = byPath.get(pt)
    const wMid = scaledWidth((ab + ba) * visualScale)
    if (wMid <= 0) continue
    edges.push(buildRibbon(a, b, ab, ba, wMid))
    flows.push(...buildFlows(a, b, ab, ba, visualScale))
  }
  return { edges, flows }
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

/** Keep only visits whose start time falls inside [t0, t1). */
export function filterVisitsByRange(visits, t0, t1) {
  const out = []
  for (const v of visits || []) {
    const t = Date.parse(v.start)
    if ((t0 == null || t >= t0) && (t1 == null || t < t1)) out.push(v)
  }
  return out
}

const UTM_PRIORITY = ['utm_campaign', 'utm_source']
const UTM_FALLBACK = ['utm_medium', 'utm_content', 'utm_term', 'utm_id']

/** Identify the source of a visit according to the requested priority. */
function identifySource(visit) {
  const utm = visit.utm || {}
  for (const k of UTM_PRIORITY) {
    const v = utm[k]
    if (v) return { value: v, isUtm: true }
  }
  if (visit.referer?.startsWith('http')) {
    return { value: visit.referer, isUtm: false }
  }
  for (const k of UTM_FALLBACK) {
    const v = utm[k]
    if (v) return { value: v, isUtm: true }
  }
  return null
}

/**
 * Collect source -> entry page pairs from visit records. Sources are
 * identified by UTM campaign/source (then referer, then other UTM tags).
 * A UTM source only gets a link href when every visit using that source
 * came from the same referer; referer sources always link to their origin.
 */
function collectSourcePairs(visits) {
  const groups = new Map() // `${source}\0${page}` -> pair
  for (const v of visits || []) {
    const src = identifySource(v)
    if (!src) continue
    const k = `${src.value}\0${v.entry}`
    const p = groups.get(k) || {
      source: src.value,
      page: v.entry,
      in: 0,
      refs: new Set(),
      missingRef: false,
      href: null,
      isUtm: src.isUtm,
    }
    p.in += 1
    if (v.referer?.startsWith('http')) {
      p.refs.add(v.referer)
    } else {
      p.missingRef = true
    }
    groups.set(k, p)
  }
  for (const p of groups.values()) {
    if (p.isUtm && !p.missingRef && p.refs.size === 1) {
      const ref = [...p.refs][0]
      if (ref.startsWith('http')) p.href = ref
    } else if (!p.isUtm && p.source.startsWith('http')) {
      p.href = p.source
    }
  }
  return [...groups.values()]
}

/**
 * Place external source and exit nodes and build their edges and bead
 * flows.
 * Sources (incoming links) are derived from visit UTM/referer data and form
 * a row centered above the map, hottest first; exits come from the
 * transition matrix and sit just outside their source page.
 * Widths and pruning use the same log scale and traffic-share rule as
 * internal connections.
 */
function buildExternal({ sources, exits }, byPath, radius, innerBounds, visualScale = 1) {
  const extNodes = []
  const edges = []
  const flows = []
  let extTotal = 0
  for (const p of sources) extTotal += p.in
  for (const p of exits) extTotal += p.out
  const minCount = extTotal * PRUNE_FRACTION
  const liveSources = sources.filter((p) => byPath.has(p.page))
  const liveExits = exits.filter((p) => byPath.has(p.page))
  if (!liveSources.length && !liveExits.length) return { extNodes, edges, flows }

  const width = (count) => scaledWidth(count * visualScale)

  const overlaps = (x, y) =>
    [...byPath.values(), ...extNodes].some(
      (n) => Math.hypot(n.x - x, n.y - y) < TNODE_BOUND + TNODE_BOUND + 10,
    )

  // Incoming: one source node per identified source, in a row centered
  // above the map, with an edge to each page that source led to.
  const bySource = new Map() // source -> pairs, sorted by total incoming count
  for (const p of liveSources.filter((p) => p.in >= minCount)) {
    const g = bySource.get(p.source) || []
    g.push(p)
    bySource.set(p.source, g)
  }
  const origins = [...bySource]
    .map(([source, ps]) => ({
      source,
      ps,
      total: ps.reduce((s, p) => s + p.in, 0),
      href: ps[0].href,
      isUtm: ps[0].isUtm,
    }))
    .sort((a, b) => b.total - a.total)
    .slice(0, MAX_EXT_IN)
  if (origins.length) {
    const cx = (innerBounds.x0 + innerBounds.x1) / 2
    const y = innerBounds.y0 - TNODE_BOUND - 64
    const spacing = TNODE_W + 44
    const x0 = cx - ((origins.length - 1) * spacing) / 2
    origins.forEach(({ source, ps, total, href, isUtm }, i) => {
      const label = isUtm ? source : extLabel(source)
      const xn = {
        path: source,
        href,
        label: label.length > 25 ? `${label.slice(0, 24)}…` : label,
        x: x0 + i * spacing,
        y,
        count: total,
        kind: 'source',
      }
      extNodes.push(xn)
      for (const p of ps) {
        const page = byPath.get(p.page)
        const wMid = width(p.in)
        if (wMid <= 0) continue
        edges.push(buildRibbon(xn, page, p.in, 0, wMid, true))
        flows.push(...buildFlows(xn, page, p.in, 0, visualScale))
      }
    })
  }

  // Outgoing: group by full URL so several links to the same domain stay
  // distinct. Each exit node is placed one ring-gap outside its source page
  // (same radial spacing internal rings use), fanned around the source angle,
  // and shows the total count across all pages that link to that URL.
  const GAP = radius(1) - radius(0)
  const outgoing = liveExits.filter((p) => p.out >= minCount)
    .sort((a, b) => b.out - a.out)
  const perPage = new Map()
  const selected = []
  for (const p of outgoing) {
    const used = perPage.get(p.page) || 0
    if (used >= MAX_EXT_OUT_PER_PAGE) continue
    perPage.set(p.page, used + 1)
    selected.push(p)
    if (selected.length >= MAX_EXT_OUT) break
  }

  const exitNodes = new Map() // full URL -> node
  const placedPerPage = new Map() // for angle fanning of the placement anchor
  for (const p of selected) {
    const page = byPath.get(p.page)
    let xn = exitNodes.get(p.ext)
    if (!xn) {
      const used = placedPerPage.get(p.page) || 0
      placedPerPage.set(p.page, used + 1)
      const base = page.depth ? page.angle : Math.PI / 2
      const ang = base + [0, 0.4, -0.4][used]
      let dist = GAP
      let x = page.x + Math.cos(ang) * dist
      let y = page.y + Math.sin(ang) * dist
      for (let tries = 0; tries < 5 && overlaps(x, y); tries++) {
        dist += GAP * 0.3
        x = page.x + Math.cos(ang) * dist
        y = page.y + Math.sin(ang) * dist
      }
      xn = {
        path: p.ext,
        href: p.ext,
        label: extLabel(p.ext),
        x,
        y,
        count: 0,
        kind: 'exit',
      }
      exitNodes.set(p.ext, xn)
      extNodes.push(xn)
    }
    xn.count += p.out
    const wMid = width(p.out)
    if (wMid <= 0) continue
    edges.push(buildRibbon(page, xn, p.out, 0, wMid, true))
    flows.push(...buildFlows(page, xn, p.out, 0, visualScale))
  }

  return { extNodes, edges, flows }
}

/**
 * Build the radial transition map model.
 * Returns { nodes, edges, flows, extNodes, arcs, bounds } or null when
 * there is nothing to show.
 */
export function buildTransitionGraph(data, pageTree, visits = [], visualScale = 1) {
  const internal = collectInternalTransitions(data?.transitions)
  const sources = collectSourcePairs(visits)
  const exits = collectExitPairs(data?.transitions)
  const navOrder = buildNavigationOrder(pageTree)
  const titles = buildTitleMap(pageTree)
  const readMinutes = buildReadMinutes(visits)

  if (!internal.length && !navOrder.size) return null

  const { nodes, byPath, root } = buildNodeTree(internal, navOrder)
  const weightFn = prepareWeights(root, navOrder)
  const unit = (2 * Math.PI) / weightFn(root)
  layoutAngles(root, unit, weightFn)

  const maxDepth = Math.max(1, ...nodes.map((n) => n.depth))
  const { radius } = positionNodes(nodes, maxDepth, unit, data?.views, titles, readMinutes)
  const arcs = buildFamilyArcs(nodes, radius)
  const pairs = aggregatePairs(internal)
  const { edges, flows } = buildInternalEdges(pairs, byPath, visualScale)

  // Tight bounding box of the actual page nodes; family ring arcs can sweep
  // outside the node pills (e.g. a large arc between two siblings on the
  // left side reaching around the right), so their geometry is included too.
  // External nodes extend the box below.
  const pad = 16
  const xs = nodes.map((n) => n.x)
  const ys = nodes.map((n) => n.y)
  const bounds = {
    x0: Math.min(...xs) - TNODE_BOUND - pad,
    y0: Math.min(...ys) - TNODE_BOUND - pad,
    x1: Math.max(...xs) + TNODE_BOUND + pad,
    y1: Math.max(...ys) + TNODE_BOUND + pad,
  }
  for (const arc of arcs) {
    if (arc.a0 == null) continue
    const b = arcBounds(arc.r, arc.a0, arc.a1)
    bounds.x0 = Math.min(bounds.x0, b.x0)
    bounds.y0 = Math.min(bounds.y0, b.y0)
    bounds.x1 = Math.max(bounds.x1, b.x1)
    bounds.y1 = Math.max(bounds.y1, b.y1)
  }

  const ext = buildExternal({ sources, exits }, byPath, radius, bounds, visualScale)
  for (const xn of ext.extNodes) {
    bounds.x0 = Math.min(bounds.x0, xn.x - TNODE_BOUND - pad)
    bounds.y0 = Math.min(bounds.y0, xn.y - TNODE_BOUND - pad)
    bounds.x1 = Math.max(bounds.x1, xn.x + TNODE_BOUND + pad)
    bounds.y1 = Math.max(bounds.y1, xn.y + TNODE_BOUND + pad)
  }

  return {
    nodes,
    edges: [...edges, ...ext.edges],
    flows: [...flows, ...ext.flows],
    extNodes: ext.extNodes,
    arcs,
    bounds,
  }
}
