// Shared in-place page re-rendering for the editor tabs: fetch a page
// without transitions and swap its dynamic regions into the live document.
// Used by BannerEditor (banner design changes), SiteEditor (theme changes)
// and StructureEditor (tree navigation).

export function runScripts(root) {
  // Scripts injected via innerHTML do not execute; re-create them.
  if (!root) return
  for (const old of root.querySelectorAll('script')) {
    const s = document.createElement('script')
    for (const a of old.attributes) s.setAttribute(a.name, a.value)
    s.textContent = old.textContent
    old.replaceWith(s)
  }
}

function swapRegions(doc) {
  for (const id of ['page-banner', 'nav', 'main']) {
    const fresh = doc.getElementById(id)
    const el = document.getElementById(id)
    if (fresh && el) el.replaceWith(document.importNode(fresh, true))
  }
  // #sidebar is omitted entirely when the section has no sub-navigation,
  // so it may be absent on either side: replace, insert, or remove.
  const freshSidebar = doc.getElementById('sidebar')
  const curSidebar = document.getElementById('sidebar')
  if (freshSidebar && curSidebar) {
    curSidebar.replaceWith(document.importNode(freshSidebar, true))
  } else if (freshSidebar) {
    document.getElementById('main')?.before(document.importNode(freshSidebar, true))
  } else if (curSidebar) {
    curSidebar.remove()
  }
  // The brand lives in the header, outside the swappable regions, and is
  // absent entirely when neither a brand nor custom brand HTML is set. A
  // plain link keeps its element (text swap only, preserving the shrink-
  // to-fit observers); anything else (custom HTML wrapper) is replaced.
  const freshBrand = doc.getElementById('brand')
  const curBrand = document.getElementById('brand')
  if (freshBrand && curBrand && freshBrand.tagName === 'A' && curBrand.tagName === 'A') {
    curBrand.textContent = freshBrand.textContent
  } else if (freshBrand && curBrand) {
    curBrand.replaceWith(document.importNode(freshBrand, true))
    runScripts(document.getElementById('brand'))
  } else if (curBrand) {
    curBrand.remove()
  } else if (freshBrand) {
    document.getElementById('nav')?.before(document.importNode(freshBrand, true))
    runScripts(document.getElementById('brand'))
  }
  // Site-wide custom CSS is in <head> and must be swapped too.
  const freshUserStyle = doc.getElementById('pagerite-user')
  const curUserStyle = document.getElementById('pagerite-user')
  if (freshUserStyle && curUserStyle) {
    curUserStyle.textContent = freshUserStyle.textContent
  } else if (freshUserStyle) {
    document.head.appendChild(document.importNode(freshUserStyle, true))
  } else if (curUserStyle) {
    curUserStyle.remove()
  }
  // Theme and other public stylesheets live in <head>, rendered with stable
  // ids by the backend; sync them positionally so the custom CSS (rendered
  // last) always keeps winning by order. Diff-based: unchanged sheets keep
  // their elements, so their @keyframes are never torn down (re-creating
  // keyframes would replay the editor's slide-in animation).
  const freshLinks = [...doc.head.querySelectorAll('link[rel="stylesheet"]')]
  const freshIds = new Set(freshLinks.map((l) => l.id))
  for (const link of [...document.head.querySelectorAll('link[rel="stylesheet"]')]) {
    if (!link.dataset.pagerite && !freshIds.has(link.id)) link.remove()
  }
  // Insert missing sheets in the fresh document's order, each right after
  // its predecessor's element. The first sheet rendered is always the base
  // CSS, so its link doubles as the fallback anchor when nothing matched yet
  // (e.g. no theme was selected before and the position is otherwise lost).
  let anchor = null
  for (const link of freshLinks) {
    const cur = link.id && document.getElementById(link.id)
    if (cur && cur.href === link.href) {
      anchor = cur
      continue
    }
    const el = document.importNode(link, true)
    // Same id, new URL (theme switch): replace in place, keeping position.
    if (cur) cur.replaceWith(el)
    else if (anchor) anchor.after(el)
    else document.getElementById('pagerite-base')?.after(el) ?? document.head.append(el)
    anchor = el
  }
  // The editor keeps its own title while open; only inherit the server title
  // when navigating outside the editor (e.g. fetch-navigation swaps).
  if (!document.body.classList.contains('editing')) {
    document.title = doc.title
  }
}

// Fetch /p, swap its regions into the live page and replaceState to it.
// Returns the final URL (after redirects), or null when the fetch did not
// yield a page. Category and missing URLs render a placeholder 404 page —
// fine to swap in (new pages are created by editing them).
export async function loadPlain(p) {
  let doc
  let finalUrl = `/${p}`
  let html
  try {
    const res = await fetch(finalUrl)
    const type = res.headers.get('content-type') || ''
    if (!type.includes('text/html')) return null
    if (res.redirected) finalUrl = res.url
    html = await res.text()
    doc = new DOMParser().parseFromString(html, 'text/html')
  } catch { return null }
  if (!doc.getElementById('main')) return null
  swapRegions(doc)
  history.replaceState(null, '', finalUrl)
  runScripts(document.getElementById('page-banner'))
  runScripts(document.getElementById('main'))
  // Keep pagerite.js's in-memory page cache in sync with the fresh copy.
  dispatchEvent(new CustomEvent('pagerite:page-fetched', { detail: { url: finalUrl, html } }))
  dispatchEvent(new CustomEvent('pagerite:preview')) // re-inject + re-tuck the edit pens
  return finalUrl
}
