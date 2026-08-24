/**
 * Radial transition map and helpers.
 *
 * Site map following the menu structure: top-level items in a row at the
 * top (below the external source row), each item's subtree fanning out
 * below it in menu order along a slightly circular downward arc. Index
 * pages with no views are omitted, their children moving up in their
 * place. All pages of the site are shown (from /_api/pages), plus any
 * extra paths seen in transitions (deleted pages); these form their own
 * top-level groups. Internal path -> path transitions join opposite
 * directions into straight connections (middle width = total
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
 * from the same referer. External exits are full-size nodes in a row below
 * the map, mirroring the source row, so the site itself stays in the
 * middle. Each distinct full exit URL is its own node. Self-loops (reload
 * pings) are skipped.
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
const MAX_EXT_OUT = 12 // exit nodes in the bottom row
const EXT_GAP = 12 // vertical margin of the source/exit rows to the map

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

/** Sort each node's children by navigation order, recursively. */
function sortByNav(root, navOrder) {
  const byNav = (a, b) =>
    (navOrder.get(a.path) ?? Infinity) - (navOrder.get(b.path) ?? Infinity)
    || a.path.localeCompare(b.path)
  const walk = (n) => {
    n.children.sort(byNav)
    n.children.forEach(walk)
  }
  walk(root)
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

/** Compute view counts, labels and hidden flags for each node. */
function annotateNodes(nodes, viewsData, titles, readMinutes) {
  const viewCount = (p) => {
    let n = 0
    for (const c of Object.values(viewsData?.[p] || {})) n += c
    return n
  }

  for (const n of nodes) {
    n.views = viewCount(n.path)
    n.readMin = readMinutes[n.path] || 0
    // Article title inside the pill (shortened with ellipsis as needed),
    // slug as fallback for pages missing from the site tree.
    const slug = n.path.split('/').pop()
    const label = titles.get(n.path) || (n.path === '/' ? '🏠︎' : slug)
    n.label = label.length > 24 ? `${label.slice(0, 23)}…` : label
    n.title = titles.get(n.path) || ''
    // Category (non-leaf) pages with no views in this window are omitted:
    // their children move up in their place (see layoutGroups).
    n.hidden = n.children.length > 0 && n.views === 0
  }
}

/**
 * Top-down layout following the menu structure: top-level items in an
 * equally spaced row at the top (right below the external source row),
 * the row following a shallow circular sag (center lowest) so connections
 * between neighbors do not overlap the pills in between. Each top item's
 * whole subtree fans out from it in menu (DFS preorder) order along a
 * large-radius circular arc that leaves the parent heading straight down
 * and gradually bends to the right — no horizontal space is reserved for fans, they
 * extend under the slots to their right. Hidden index pages are omitted
 * from the fan; when the top item itself is hidden, the fan shifts one
 * slot up, the first visible child taking the top position. Branch lanes
 * labeled with the branch slug (see the branch-lane pass at the end)
 * keep the omitted menu levels visible.
 */
function layoutGroups(root) {
  // Top slots are spaced well over one pill width apart regardless of
  // fan sizes.
  const SLOT = TNODE_W + 100
  const CLEAR = TNODE_W * 0.8 // fan spacing per member along the curve

  // First pass: visible members per group, in menu order. Hidden index
  // pages are skipped, but their children still appear. The front page
  // forms its own group. groupRoots keeps each group's subtree root for
  // the branch-curve pass below.
  const groups = []
  const groupRoots = []
  for (const g of [root, ...root.children]) {
    const members = []
    if (g === root) {
      if (!g.hidden) members.push(g)
    } else {
      const walk = (n) => {
        if (!n.hidden) members.push(n)
        n.children.forEach(walk)
      }
      walk(g)
    }
    if (members.length) {
      groups.push(members)
      groupRoots.push(g)
    }
  }

  // Top row on a large-radius circular arc whose bottom point is the
  // LAST top item: each earlier item sits a bit higher (drop = 15% of
  // the row span). Flat row when there is a single group.
  const half = ((groups.length - 1) * SLOT) / 2 || 1
  const span = (groups.length - 1) * SLOT
  const topD = span * 0.15
  const R_T = span ? (span * span + topD * topD) / (2 * topD) : 0
  const topY = span
    ? (x) => topD - R_T + Math.sqrt(R_T * R_T - (x - half) * (x - half))
    : () => 0

  // Second pass: place groups. Fan members follow a circular arc of
  // large radius FAN_R centered at (gx + FAN_R, y0): the trail leaves
  // the top node heading straight down (vertical tangent) and bends
  // right gently, member i at arc angle π − i·CLEAR/FAN_R (spaced by
  // arc length CLEAR). A circle — not a spline — so the branch lanes
  // below can be concentric arcs: identical forms, only radii differ.
  const FAN_R = 1000
  groups.forEach((members, gi) => {
    const gx = gi * SLOT - half
    const y0 = topY(gx)
    members[0].x = gx
    members[0].y = y0
    for (let i = 1; i < members.length; i++) {
      const th = Math.PI - (i * CLEAR) / FAN_R
      members[i].x = gx + FAN_R * (1 + Math.cos(th))
      members[i].y = y0 + FAN_R * Math.sin(th)
    }
  })

  // Branch lanes: one wide arc per path prefix (slug depth ≥ 1) whose
  // subtree holds at least two visible nodes (a branch's visible nodes
  // form one contiguous run in the fan's DFS preorder). Every lane of a
  // group is an arc around the group's fan center with a radius one
  // INDENT larger per parent level — concentric circles, so all lanes
  // share exactly one form. Lanes span their branch's nodes plus a
  // little extra tucked under the first/last pill (so the line caps are
  // never visible) and run behind the pills. A separate short arc
  // across the first inter-node gap carries the branch slug as a label,
  // replacing per-node path crumbs. Hidden (unplaced) index pages still
  // define a lane: it follows their promoted children, so lanes reflect
  // the path structure rather than page existence.
  const INDENT = 20 // lane spacing (radius) per nesting level (> lane width)
  const END_TUCK = 22 // arc units tucked under the first/last pill
  const GAP_TRIM = 32 // label arc clearance from the pills
  const LABEL_CHARS = 8 // ~13px glyphs fitting the gap
  const branches = []
  groups.forEach((members, gi) => {
    const g = groupRoots[gi]
    if (g === root || members.length < 2) return
    const idx = new Map(members.map((n, i) => [n, i]))
    const C = [gi * SLOT - half + FAN_R, topY(gi * SLOT - half)]
    const walk = (n) => {
      let first = Infinity
      let last = -1
      const span = (m) => {
        const k = idx.get(m)
        if (k !== undefined) {
          first = Math.min(first, k)
          last = Math.max(last, k)
        }
        m.children.forEach(span)
      }
      span(n)
      if (n.depth >= 1 && last > first) {
        branches.push({ depth: n.depth, name: n.path.split('/').pop(), C, first, last })
      }
      n.children.forEach(walk)
    }
    walk(g)
  })
  const depthMax = branches.reduce((d, b) => Math.max(d, b.depth), 1)
  let arcLeft = Infinity // leftmost lane point, for the bounding box
  const arcs = branches.map(({ depth, name, C, first, last }) => {
    const R = FAN_R + (depthMax - depth) * INDENT
    const th = (i) => Math.PI - (i * CLEAR) / FAN_R
    const pt = (a, r) => [C[0] + r * Math.cos(a), C[1] + r * Math.sin(a)]
    // Arc from angle a down to angle b (a > b; visually counterclockwise
    // from the west point downward, hence sweep flag 0).
    const arc = (a, b, r) => {
      const [x0, y0] = pt(a, r)
      const [x1, y1] = pt(b, r)
      return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r.toFixed(2)} ${r.toFixed(2)} 0 0 0 ${x1.toFixed(2)} ${y1.toFixed(2)}`
    }
    const d = arc(th(first) + END_TUCK / R, th(last) - END_TUCK / R, R)
    // The label guide rides GUIDE_OFF outward of the lane centerline: the
    // text's alphabetic baseline sits on the guide, so this puts the
    // glyph middle (not the baseline) on the lane center at any zoom —
    // dominant-baseline tricks are em-based and break under downscale.
    const GUIDE_OFF = 3.5
    const ld = arc(th(first) - GAP_TRIM / R, th(first + 1) + GAP_TRIM / R, R + GUIDE_OFF)
    arcLeft = Math.min(arcLeft, pt(th(first) + END_TUCK / R, R)[0])
    const label = name.length > LABEL_CHARS ? `${name.slice(0, LABEL_CHARS - 1)}…` : name
    return { d, ld, label }
  })
  // Top lane: an unlabeled arc along the top row's own circle, connecting
  // the top nodes of all groups and tucked under the first and last of
  // them (the arc bottoms at the last item, so it continues rightward
  // under its pill). Drawn 50% thicker than branch lanes.
  if (span) {
    arcs.unshift({
      d: `M ${(-half - END_TUCK).toFixed(2)} ${topY(-half - END_TUCK).toFixed(2)} `
       + `A ${R_T.toFixed(2)} ${R_T.toFixed(2)} 0 0 0 ${(half + END_TUCK).toFixed(2)} ${topY(half + END_TUCK).toFixed(2)}`,
      ld: null,
      label: null,
      top: true,
    })
  }
  return { arcs, arcLeft }
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
    if (a.hidden || b.hidden) continue // unplaced index pages are omitted
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
 * transition matrix and form a matching row centered below the map, so
 * the site itself stays in the middle. Both rows sit EXT_GAP beyond the
 * map's bounds.
 * Widths and pruning use the same log scale and traffic-share rule as
 * internal connections.
 */
function buildExternal({ sources, exits }, byPath, innerBounds, visualScale = 1) {
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
    const y = innerBounds.y0 - TNODE_BOUND - EXT_GAP
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
        if (page.hidden) continue
        const wMid = width(p.in)
        if (wMid <= 0) continue
        edges.push(buildRibbon(xn, page, p.in, 0, wMid, true))
        flows.push(...buildFlows(xn, page, p.in, 0, visualScale))
      }
    })
  }

  // Outgoing: one exit node per distinct full URL (so several links to
  // the same domain stay distinct), showing the total count across all
  // pages linking to it, in a row centered below the map (hottest
  // first), mirroring the source row above. Each (URL, page) pair
  // contributes an edge from that page.
  const byExt = new Map() // full URL -> { ext, out, pairs }
  for (const p of liveExits.filter((p) => p.out >= minCount)) {
    const g = byExt.get(p.ext) || { ext: p.ext, out: 0, pairs: [] }
    g.out += p.out
    g.pairs.push(p)
    byExt.set(p.ext, g)
  }
  const targets = [...byExt.values()]
    .sort((a, b) => b.out - a.out)
    .slice(0, MAX_EXT_OUT)
  if (targets.length) {
    const cx = (innerBounds.x0 + innerBounds.x1) / 2
    const y = innerBounds.y1 + TNODE_BOUND + EXT_GAP
    const spacing = TNODE_W + 44
    const x0 = cx - ((targets.length - 1) * spacing) / 2
    targets.forEach(({ ext, out, pairs }, i) => {
      const xn = {
        path: ext,
        href: ext,
        label: extLabel(ext),
        x: x0 + i * spacing,
        y,
        count: out,
        kind: 'exit',
      }
      extNodes.push(xn)
      for (const p of pairs) {
        const page = byPath.get(p.page)
        if (page.hidden) continue
        const wMid = width(p.out)
        if (wMid <= 0) continue
        edges.push(buildRibbon(page, xn, p.out, 0, wMid, true))
        flows.push(...buildFlows(page, xn, p.out, 0, visualScale))
      }
    })
  }

  return { extNodes, edges, flows }
}

/**
 * Build the transition map model.
 * Returns { nodes, edges, flows, extNodes, arcs, bounds } or null when
 * there is nothing to show. `arcs` holds the branch curves; `nodes` only
 * contains placed (visible) nodes.
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
  sortByNav(root, navOrder)
  annotateNodes(nodes, data?.views, titles, readMinutes)
  const { arcs, arcLeft } = layoutGroups(root)
  const placed = nodes.filter((n) => !n.hidden)
  const pairs = aggregatePairs(internal)
  const { edges, flows } = buildInternalEdges(pairs, byPath, visualScale)

  // Tight bounding box of the placed page nodes, extended to cover the
  // branch curves running left of the pills; external nodes extend it.
  const pad = 16
  const xs = placed.map((n) => n.x)
  const ys = placed.map((n) => n.y)
  const bounds = {
    x0: Math.min(Math.min(...xs) - TNODE_BOUND, arcLeft) - pad,
    y0: Math.min(...ys) - TNODE_BOUND - pad,
    x1: Math.max(...xs) + TNODE_BOUND + pad,
    y1: Math.max(...ys) + TNODE_BOUND + pad,
  }

  const ext = buildExternal({ sources, exits }, byPath, bounds, visualScale)
  for (const xn of ext.extNodes) {
    bounds.x0 = Math.min(bounds.x0, xn.x - TNODE_BOUND - pad)
    bounds.y0 = Math.min(bounds.y0, xn.y - TNODE_BOUND - pad)
    bounds.x1 = Math.max(bounds.x1, xn.x + TNODE_BOUND + pad)
    bounds.y1 = Math.max(bounds.y1, xn.y + TNODE_BOUND + pad)
  }

  return {
    nodes: placed,
    edges: [...edges, ...ext.edges],
    flows: [...flows, ...ext.flows],
    extNodes: ext.extNodes,
    arcs,
    bounds,
  }
}
