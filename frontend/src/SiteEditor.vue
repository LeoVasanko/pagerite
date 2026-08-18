<script setup>
// Site editor: site-wide brand, banner HTML for the current page
// (previewed into the real #page-banner region, so you see exactly which
// banner you're editing) and the draggable site structure tree. Opened
// from the pen on the banner. Everything saves immediately as you edit —
// no save button, no edit mode. Focusing a page's row navigates to it in
// place (no transitions).
//
// The tree comes from the server nested (GET /_api/pages); every node is
// real — a label with a title and slug, with content (landing page) or
// without (category whose URL renders a placeholder page). The front page
// is a top-level row with an empty slug, not the parent of the others.
import { computed, onMounted, onUnmounted, provide, ref } from 'vue'
import StructureTree from './StructureTree.vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState, Compartment } from '@codemirror/state'
import { placeholder } from '@codemirror/view'
import { css } from '@codemirror/lang-css'
import { html } from '@codemirror/lang-html'
import { cmHighlight, cmTheme } from './cmtheme'
import { slugify } from './slugify'

const props = defineProps({
  pagePath: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const path = ref('')
const banner = ref('')
const saveError = ref('')
const tree = ref([])
const fileInput = ref(null)
const bannerEl = ref(null)

let ws = null
let pendingSave = null
let reconnectTimer = null
let reconnectDelay = 2000
const MAX_RECONNECT_DELAY = 16000
let everConnected = false
let view = null      // CodeMirror for the banner HTML
let syncing = false  // set while replacing the document programmatically
const bannerPh = new Compartment()  // placeholder shows the inherited source

let cssView = null      // CodeMirror for the site-wide custom CSS
let cssSyncing = false  // set while replacing the CSS document programmatically
const customCss = ref('')
const cssEl = ref(null)

// path -> node, for quick lookups (current title, delete checks).
const flatMap = computed(() => {
  const map = {}
  const walk = (nodes) => {
    for (const n of nodes) {
      map[n.path] = n
      walk(n.children)
    }
  }
  walk(tree.value)
  return map
})

function normPath(p) {
  return p.trim().replace(/^\/+|\/+$/g, '')
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg))
  } else {
    ensureConnected()
  }
}

function ensureConnected() {
  if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
    connect()
  }
}

// Debounce per key: text edits save while typing, without a request per
// keystroke.
const timers = {}
function debounce(key, fn, ms = 600) {
  clearTimeout(timers[key])
  timers[key] = setTimeout(fn, ms)
}

// Banner saves are fire-and-forget, with the pending save resent if the
// socket reconnects mid-edit.
function save() {
  const msg = { type: 'save', path: normPath(path.value), banner: banner.value }
  pendingSave = msg
  send(msg)
}

function close() {
  emit('close')
}

function openPath(p) {
  path.value = p
  send({ type: 'open', path: p })
}

// --- In-place navigation (no transitions, replaceState) ------------------
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
  // The brand link lives in the header, outside the swappable regions,
  // and is absent entirely when no brand is configured.
  const freshBrand = doc.getElementById('brand')
  const curBrand = document.getElementById('brand')
  if (freshBrand && curBrand) {
    curBrand.textContent = freshBrand.textContent
  } else if (curBrand) {
    curBrand.remove()
  } else if (freshBrand) {
    document.getElementById('nav')?.before(document.importNode(freshBrand, true))
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
  document.title = doc.title
}

async function loadPlain(p) {
  let doc
  let finalUrl = `/${p}`
  try {
    const res = await fetch(finalUrl)
    const type = res.headers.get('content-type') || ''
    if (!type.includes('text/html')) return
    // Category and missing URLs render a placeholder 404 page — fine to
    // swap in (new pages are created by editing them).
    if (res.redirected) finalUrl = res.url
    doc = new DOMParser().parseFromString(await res.text(), 'text/html')
  } catch { return }
  if (!doc.getElementById('main')) return
  swapRegions(doc)
  history.replaceState(null, '', finalUrl)
  runScripts(document.getElementById('page-banner'))
  runScripts(document.getElementById('main'))
  dispatchEvent(new CustomEvent('pagerite:preview')) // re-inject + re-tuck the edit pens
  // The swap brought in the server-rendered (inherited) banner; overlay
  // the page's own banner if one is being edited.
  if (banner.value.trim()) previewBanner()
}

// Tree row focus: switch the edited page and show it, skipping transitions.
async function navigate(p) {
  openPath(p)
  await loadPlain(p)
}

// If the currently edited page moved (rename/move of itself or an
// ancestor), follow it to the new path.
function followMove(oldPath, newPath) {
  if (path.value === oldPath) navigate(newPath)
  else if (oldPath && path.value.startsWith(`${oldPath}/`)) {
    navigate(newPath + path.value.slice(oldPath.length))
  }
}

// --- New page flow -------------------------------------------------------
// The ➕ row at the end of any list adds a *pending* row there: a
// local-only item that can be dragged into place before anything is
// filled in. It is persisted only on commit (✓/Enter), at wherever it
// currently sits.
const pending = ref(null)

function newPage(list) {
  if (pending.value) return // one at a time
  pending.value = {
    slug: '',
    path: '',
    title: '',
    order: 0,
    published: true,
    has_content: true,
    children: [],
    pending: true,
  }
  list.push(pending.value)
}

// Where does the pending row currently sit? -> {parentPath, list, index}.
function locatePending(nodes, parentPath) {
  const i = nodes.indexOf(pending.value)
  if (i >= 0) return { parentPath, list: nodes, i }
  for (const n of nodes) {
    const found = locatePending(n.children, n.path)
    if (found) return found
  }
  return null
}

function discardPending() {
  const loc = locatePending(tree.value, '')
  if (loc) loc.list.splice(loc.i, 1)
  pending.value = null
}

async function commitPending() {
  const node = pending.value
  if (!node) return
  // Empty slug: derive one from the title (transliterated to ASCII).
  const slug = slugify(node.slug.trim()) || slugify(node.title)
  if (!slug) {
    return
  }
  const loc = locatePending(tree.value, '')
  const parentPath = loc?.parentPath ?? ''
  const newPath = parentPath ? `${parentPath}/${slug}` : slug
  const res = await fetch(`/_api/pages/${newPath}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      title: node.title.trim() || slug,
      markdown: '', // empty markdown creates an empty page (never deletes)
      published: true,
    }),
  })
  if (!res.ok) {
    // Show the server's reason (e.g. a reserved file name); the pending
    // row stays so it can be edited and committed again.
    saveError.value = `⚠️ ${await errorDetail(res)}`
    return
  }
  // Place it exactly where the row was dropped: a fresh order key halfway
  // between its new siblings (the PUT appended it at the end).
  if (loc) {
    const prev = loc.list[loc.i - 1]
    const next = loc.list[loc.i + 1]
    const order = prev && next ? (prev.order + next.order) / 2
      : prev ? prev.order + 1
      : next ? next.order - 1
      : 1
    await postStructure({ path: newPath, order })
  } else {
    saveError.value = ''
  }
  pending.value = null
  await refreshPages()
  await navigate(newPath)
  // Hand over to the page editor for the actual writing: click the fresh
  // page's pen (pagerite.js swaps the panel; CodeMirror focuses on mount).
  document.querySelector('#main article button.edit-link')?.click()
}

// --- Site-wide brand (header link + <title> suffix) ----------------------
// Edits apply to the live page immediately and save while typing. An
// empty brand removes the header link and the title suffix entirely.
const brand = ref('')
const theme = ref('purple')
// Theme and banner-design options come from the backend (theme folders on
// disk, see GET /_api/settings), so added themes need no frontend changes.
const themeOptions = ref([{ value: '', label: 'none' }])
const bannerDesigns = ref([])

async function loadSettings() {
  try {
    const s = await (await fetch('/_api/settings')).json()
    brand.value = s.brand
    theme.value = s.theme || ''
    customCss.value = s.custom_css || ''
    favicon.value = s.favicon || ''
    themeOptions.value = [
      { value: '', label: 'none' },
      ...(s.themes || []).map((t) => ({ value: t, label: t })),
    ]
    bannerDesigns.value = s.banner_designs || []
  } catch { /* keep default */ }
}

// --- Favicon ---------------------------------------------------------------
// Uploaded into the content-addressed file store (PUT /_api/settings/favicon)
// and linked on every page as <link rel="icon">; empty falls back to the
// build's /favicon.ico. Applies to the live page immediately.
const favicon = ref('')
const faviconInput = ref(null)

function applyFavicon(url) {
  let link = document.querySelector('link[rel="icon"]')
  if (url) {
    if (!link) {
      link = document.createElement('link')
      link.rel = 'icon'
      link.id = 'pagerite-favicon'
      document.head.append(link)
    }
    link.href = url
  } else if (link?.id === 'pagerite-favicon') {
    link.remove()
  }
}

async function uploadFavicon(file) {
  if (!file || !file.type.startsWith('image/')) return
  const res = await fetch('/_api/settings/favicon', {
    method: 'PUT',
    headers: { 'x-filename': file.name.replace(/[^\w.-]/g, '-') },
    body: file,
  })
  if (res.ok) {
    saveError.value = ''
    const { path: url } = await res.json()
    favicon.value = url
    applyFavicon(url)
  } else {
    saveError.value = `⚠️ ${await errorDetail(res)}`
  }
}

async function removeFavicon() {
  const res = await fetch('/_api/settings/favicon', { method: 'DELETE' })
  if (res.ok) {
    saveError.value = ''
    favicon.value = ''
    applyFavicon('')
  } else {
    saveError.value = `⚠️ ${await errorDetail(res)}`
  }
}

function currentTitle() {
  return flatMap.value[path.value]?.title
    || document.title.replace(/ – [^–]*$/, '')
}

function applyBrand(b) {
  let el = document.getElementById('brand')
  if (b) {
    if (!el) {
      el = document.createElement('a')
      el.id = 'brand'
      el.href = '/'
      document.getElementById('nav')?.before(el)
    }
    el.textContent = b
    document.title = `${currentTitle()} – ${b}`
  } else {
    if (el) el.remove()
    document.title = currentTitle()
  }
}

function onBrandInput() {
  applyBrand(brand.value)
  debounce('brand', saveBrand)
}

async function saveSettings(opts = {}) {
  const res = await fetch('/_api/settings', {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      brand: brand.value,
      theme: theme.value,
      custom_css: customCss.value,
      ...opts,
    }),
  })
  if (res.ok) {
    saveError.value = ''
  } else {
    saveError.value = '⚠️ changes could not be saved'
  }
}

function saveBrand() {
  saveSettings()
}

async function onThemeChange() {
  await saveSettings()
  // Theme CSS is backend-served at /_themes/{theme}/theme.css in both dev
  // and prod: swap the link in place, then re-render (the theme's default
  // banner design and the page's stylesheet links may change with it).
  let link = document.getElementById('pagerite-theme')
  if (theme.value) {
    const href = `/_themes/${theme.value}/theme.css`
    if (link) {
      link.href = href
    } else {
      // Re-create after "none": keep base < theme < design < custom CSS.
      // In dev there is no #pagerite-base link (the base is a
      // Vite-injected <style>), so anchor to the next sheet instead of
      // prepending before the base styles.
      link = document.createElement('link')
      link.rel = 'stylesheet'
      link.id = 'pagerite-theme'
      link.href = href
      const before = document.getElementById('pagerite-base')?.nextSibling
        ?? document.getElementById('pagerite-banner')
        ?? document.getElementById('pagerite-user')
      if (before) before.before(link)
      else document.head.append(link)
    }
  } else if (link) {
    link.remove()
  }
  loadPlain(path.value)
}

// --- Site-wide custom CSS --------------------------------------------------
// Edits apply to the live page immediately and save while typing.
function applyCustomCss(css) {
  let el = document.getElementById('pagerite-user')
  if (css.trim()) {
    if (!el) {
      el = document.createElement('style')
      el.id = 'pagerite-user'
    }
    el.textContent = css
    // Keep it last in <head>: in dev Vite injects the base stylesheet
    // after the server-rendered tag, and equal-specificity :root rules
    // (font variables) are decided by order.
    document.head.append(el)
  } else if (el) {
    el.remove()
  }
}

function onCustomCssInput() {
  // Keep the font picker selection in sync with manual edits to the CSS.
  parseFonts(customCss.value)
  applyCustomCss(customCss.value)
  debounce('custom-css', saveCustomCss, 400)
}

function saveCustomCss() {
  saveSettings()
}

function setCssDocument(text) {
  cssSyncing = true
  cssView.dispatch({ changes: { from: 0, to: cssView.state.doc.length, insert: text } })
  cssSyncing = false
  customCss.value = text
}

// --- Font overrides --------------------------------------------------------
// Font picks live in the custom CSS as plain :root rows in one exact format
// (`  --font-body: var(--font-source-sans);`), so no marker comments are
// needed: the rows are parsed out on load, and on change they are stripped
// and rewritten — adding a :root block if none exists, dropping it when the
// last font row goes away. Values reference the per-family variables from
// pagerite.css, so no font stacks are spelled out here.
// Declaration-level match (not line-anchored): user-edited rows may sit
// inline with other content, and those must be stripped/parsed too or
// re-picking a font would insert a duplicate row.
const FONT_DECL = /--font-(?:body|heading|brand)\s*:\s*var\(--font-[a-z0-9-]+\)\s*;/g
const FONT_OPTIONS = [
  { value: 'var(--font-source-serif)', label: 'Source Serif 4', serif: true },
  { value: 'var(--font-fraunces)', label: 'Fraunces', serif: true },
  { value: 'var(--font-literata)', label: 'Literata', serif: true },
  { value: 'var(--font-cormorant)', label: 'Cormorant', serif: true },
  { value: 'var(--font-playfair)', label: 'Playfair Display', serif: true },
  { value: 'var(--font-new-rocker)', label: 'New Rocker', serif: true },
  { value: 'var(--font-source-sans)', label: 'Source Sans 3', serif: false },
  { value: 'var(--font-inter)', label: 'Inter', serif: false },
  { value: 'var(--font-montserrat)', label: 'Montserrat', serif: false },
  { value: 'var(--font-cause)', label: 'Cause', serif: false },
  { value: 'var(--font-exo2)', label: 'Exo 2', serif: false },
  { value: 'var(--font-fira-code)', label: 'Fira Code', serif: false },
]
const fontHeading = ref('')
const fontBody = ref('')
const fontBrand = ref('')

// Font picker popup: a stylized "A" opens a panel with a tab per target
// (heading/body/brand); each option's name is its own preview, rendered in
// the candidate font at the size and weight of the element being styled.
const fontPicker = ref(null) // open tab: 'heading' | 'body' | 'brand' | null
let fontTabLast = 'body'
const serifFonts = computed(() => FONT_OPTIONS.filter((o) => o.serif))
const sansFonts = computed(() => FONT_OPTIONS.filter((o) => !o.serif))

function toggleFontPanel() {
  if (fontPicker.value) {
    fontTabLast = fontPicker.value
    fontPicker.value = null
  } else {
    fontPicker.value = fontTabLast
  }
}

function fontRefFor(name) {
  return { heading: fontHeading, body: fontBody, brand: fontBrand }[name]
}

function fontStyleFor(name) {
  if (name === 'brand') return { fontWeight: 700, fontSize: '1.5rem' }
  if (name === 'heading') return { fontWeight: 600, fontSize: '1.3rem' }
  return {}
}

function pickFont(value) {
  const r = fontRefFor(fontPicker.value)
  // Clicking the current pick clears it back to the base-style default.
  r.value = r.value === value ? '' : value
  onFontChange()
}

function fontRows() {
  const rows = []
  if (fontBody.value) rows.push(`  --font-body: ${fontBody.value};`)
  if (fontHeading.value) rows.push(`  --font-heading: ${fontHeading.value};`)
  if (fontBrand.value) rows.push(`  --font-brand: ${fontBrand.value};`)
  return rows
}

function onFontChange() {
  const rows = fontRows()
  // Strip our declarations wherever they are (even inline with other
  // content), drop lines they emptied, then drop :root blocks left empty.
  let css = customCss.value
    .split('\n')
    .map((l) => {
      const stripped = l.replace(FONT_DECL, '')
      return stripped === l ? l : stripped.trim() ? stripped : null
    })
    .filter((l) => l !== null)
    .join('\n')
    .replace(/:root\s*\{\s*\}\n?/g, '')
  if (rows.length) {
    if (/:root\s*\{/.test(css)) {
      // Merge into the existing :root block.
      css = css.replace(/:root\s*\{/, (m) => `${m}\n${rows.join('\n')}`)
    } else {
      const rest = css.trimStart()
      css = `:root {\n${rows.join('\n')}\n}\n${rest ? `\n${rest}` : ''}`
    }
  }
  setCssDocument(css)
  onCustomCssInput()
}

function parseFonts(css) {
  const get = (name) =>
    css.match(new RegExp(`--font-${name}\\s*:\\s*(var\\(--font-[a-z0-9-]+\\))\\s*;`))?.[1] || ''
  fontBody.value = get('body')
  fontHeading.value = get('heading')
  fontBrand.value = get('brand')
}

// Two-step delete (no dialogs): the first click arms the row's button for
// a few seconds, the second actually deletes.
const arming = ref(null)
let armTimer = null

function armRemove(node) {
  if (arming.value === node.path) {
    clearTimeout(armTimer)
    arming.value = null
    removePage(node)
  } else {
    arming.value = node.path
    clearTimeout(armTimer)
    armTimer = setTimeout(() => { arming.value = null }, 3000)
  }
}

async function removePage(node) {
  const res = await fetch(`/_api/pages/${node.path}`, { method: 'DELETE' })
  if (res.ok) {
    saveError.value = ''
    refreshPages()
    const p = node.path
    if (p === path.value || (p && path.value.startsWith(`${p}/`))) {
      // The current page was deleted — or reduced to a category, which now
      // renders a placeholder page. Either way, re-render from the server.
      if (node.children.length) loadPlain(path.value)
      else navigate('')
    } else {
      loadPlain(path.value) // refresh menus
    }
  } else {
    saveError.value = '⚠️ changes could not be saved'
  }
}

// --- Site structure tree (drag-and-drop ordering/moving) ----------------
async function refreshPages() {
  try {
    tree.value = await (await fetch('/_api/pages')).json()
  } catch { /* list stays stale; not fatal */ }
}

// Human-readable reason from a failed API call (FastAPI errors carry a
// JSON {detail}), falling back to a generic message.
async function errorDetail(res) {
  const body = await res.json().catch(() => null)
  return body?.detail || 'changes could not be saved'
}

async function postStructure(op) {
  const res = await fetch('/_api/structure', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(op),
  })
  if (res.ok) {
    saveError.value = ''
    loadPlain(path.value) // refresh menus and content from the server
  } else {
    saveError.value = `⚠️ ${await errorDetail(res)}`
  }
  await refreshPages()
  return res.ok
}

async function onReorder(parentPath, list, evt) {
  // vuedraggable already mutated `list`; persist the moved item only: a
  // fresh order key halfway between its new siblings (all other items
  // keep theirs), plus the new path when the parent changed. The pending
  // new-page row is local-only — its position is read at commit time.
  const change = evt.moved || evt.added
  if (!change) return
  const el = change.element
  if (el.pending) return
  const i = change.newIndex
  let prev, next
  for (let j = i - 1; j >= 0 && !prev; j--) if (!list[j].pending) prev = list[j]
  for (let j = i + 1; j < list.length && !next; j++) if (!list[j].pending) next = list[j]
  const order = prev && next ? (prev.order + next.order) / 2
    : prev ? prev.order + 1
    : next ? next.order - 1
    : 1
  const newPath = parentPath ? `${parentPath}/${el.slug}` : el.slug
  const op = { path: el.path, order }
  if (newPath !== el.path) op.move_to = newPath
  if (await postStructure(op) && op.move_to) followMove(el.path, op.move_to)
}

// Inline title/slug editing: rows are always editable. Title saves while
// typing (debounced); the slug commits on blur/Enter, since it renames
// the path (moving the whole subtree with it).
function onTitleInput(node, ev) {
  const title = ev.target.value.trim()
  if (!title || title === node.title) return
  debounce(`title:${node.path}`, async () => {
    await postStructure({ path: node.path, title })
  })
}

// The slug inputs are filtered as you type (StructureTree onSlugInput,
// see slugify.js); the server re-validates and its reason is shown.
async function commitSlug(node, ev) {
  const slug = ev.target.value.trim()
  if (slug === node.slug) return
  const parent = node.path.split('/').slice(0, -1).join('/')
  // Empty slug at top level = the front page (path "").
  const moveTo = parent ? (slug ? `${parent}/${slug}` : parent) : slug
  if (await postStructure({ path: node.path, move_to: moveTo })) {
    followMove(node.path, moveTo)
  } else {
    ev.target.value = node.slug // rename failed: put the old slug back
  }
}

provide('structureHandlers', {
  current: () => path.value,
  open: navigate,
  arming: () => arming.value,
  armRemove,
  reorder: onReorder,
  titleInput: onTitleInput,
  commitSlug,
  commitPending,
  discardPending,
  newPage,
})

// --- Banner design ---------------------------------------------------------
// The page's banner design: null = inherit (nearest ancestor's setting,
// then the theme's default), '' = explicitly none, otherwise a design
// name. bannerDesignFrom tells where an inherited setting comes from
// (null = the theme default), shown in the selector's inherit option.
const bannerDesign = ref(null)
const bannerDesignFrom = ref(null)

const inheritLabel = computed(() => {
  if (bannerDesignFrom.value === null) {
    return `inherit (theme: ${theme.value || 'none'})`
  }
  return `inherit (/${bannerDesignFrom.value})`
})

function onBannerDesignChange() {
  // Saves immediately; the preview needs a server re-render (the design's
  // inline SVG and its stylesheet link both change).
  const msg = {
    type: 'save',
    path: normPath(path.value),
    banner_design: bannerDesign.value,
  }
  pendingSave = msg
  send(msg)
  loadPlain(path.value)
}

// --- Banner editing ------------------------------------------------------
// The banner HTML is edited in a small CodeMirror window (HTML syntax),
// previewed into the real #page-banner region on every keystroke.
function setDocument(text) {
  syncing = true
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } })
  syncing = false
  banner.value = text
}

function runScripts(root) {
  // Scripts injected via innerHTML do not execute; re-create them.
  for (const old of root.querySelectorAll('script')) {
    const s = document.createElement('script')
    for (const a of old.attributes) s.setAttribute(a.name, a.value)
    s.textContent = old.textContent
    old.replaceWith(s)
  }
}

function previewBanner() {
  const el = document.getElementById('page-banner')
  if (!el) return
  if (banner.value.trim()) {
    // Own banner code supplements the design: the inlined design artwork
    // (marked with [data-design]) is detached while the author code is
    // swapped in (so runScripts never re-runs the design's own scripts),
    // then put back first — author code stays last so its styles win.
    const artwork = [...el.querySelectorAll('[data-design]')]
    for (const a of artwork) a.remove()
    el.innerHTML = banner.value
    runScripts(el)
    el.prepend(...artwork)
  } else {
    // No banner code of its own: the region must show the inherited/design
    // banner — re-render from the server (an empty write here would wipe it).
    loadPlain(path.value)
  }
}

function onBannerInput() {
  previewBanner()
  debounce('banner-html', save, 400)
}

function stripBannerMedia(html) {
  // A banner has one piece of media: uploading replaces earlier img/video
  // tags instead of stacking them. (Other HTML, e.g. canvas+script, stays.)
  const doc = new DOMParser().parseFromString(html, 'text/html')
  for (const el of doc.querySelectorAll('img, video')) el.remove()
  return doc.body.innerHTML.trim()
}

async function uploadBannerMedia(file) {
  // Banner media goes to the shared content store, like article images.
  if (!file || !/^(image|video)\//.test(file.type)) return
  const name = file.name.replace(/[^\w.-]/g, '-')
  const res = await fetch(`/_api/files/${encodeURIComponent(name)}`, { method: 'PUT', body: file })
  if (!res.ok) {
    return
  }
  const { path: stored } = await res.json()
  const tag = file.type.startsWith('video/')
    ? `<video src="${stored}" autoplay muted loop playsinline></video>`
    : `<img src="${stored}" alt="">`
  const rest = stripBannerMedia(banner.value)
  setDocument(rest ? `${tag}\n${rest}` : tag)
  previewBanner()
  save()
}

function onBannerPaste(ev) {
  const file = [...(ev.clipboardData?.files || [])]
    .find((f) => /^(image|video)\//.test(f.type))
  if (file) {
    ev.preventDefault()
    uploadBannerMedia(file)
  }
}

function onMessage(ev) {
  const msg = JSON.parse(ev.data)
  if (msg.type === 'doc' && msg.path === path.value) {
    setDocument(msg.banner ?? '')
    bannerDesign.value = msg.banner_design ?? null
    bannerDesignFrom.value = msg.banner_design_from ?? null
    // Placeholder tells where an empty banner code field falls back to;
    // the design artwork renders regardless (this code supplements it).
    view.dispatch({
      effects: bannerPh.reconfigure(placeholder(
        msg.banner_from == null
          ? 'own banner code (added after the design)'
          : `code inherited from /${msg.banner_from}`,
      )),
    })
    // Overlay this page's own banner on the swapped region. Empty means
    // inherited: the server-rendered region already shows the right one.
    if (banner.value.trim()) previewBanner()
  } else if (msg.type === 'saved') {
    saveError.value = ''
    pendingSave = null
    refreshPages()
  } else if (msg.type === 'error') {
    saveError.value = '⚠️ changes could not be saved'
  }
}

function onKeydown(ev) {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === 's') {
    ev.preventDefault()
    save()
  }
  if (ev.key === 'Escape') close()
}

function connect() {
  ws = new WebSocket(
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/_api/ws/editor`,
  )
  ws.onmessage = onMessage
  ws.onopen = () => {
    reconnectDelay = 2000
    if (everConnected) {
      // Reconnected: resend any save attempted while offline.
      if (pendingSave) send(pendingSave)
    } else {
      openPath(normPath(props.pagePath))
    }
    everConnected = true
  }
  ws.onclose = () => {
    clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => {
      connect()
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY)
    }, reconnectDelay)
  }
}

onMounted(async () => {
  refreshPages()
  connect()
  view = new EditorView({
    state: EditorState.create({
      doc: '',
      extensions: [
        basicSetup,
        html(),
        cmTheme,
        cmHighlight,
        EditorView.lineWrapping,
        bannerPh.of(placeholder('')),
        EditorView.updateListener.of((u) => {
          if (u.docChanged && !syncing) {
            banner.value = view.state.doc.toString()
            onBannerInput()
          }
        }),
      ],
    }),
    parent: bannerEl.value,
  })
  cssView = new EditorView({
    state: EditorState.create({
      doc: '',
      extensions: [
        basicSetup,
        css(),
        cmTheme,
        cmHighlight,
        EditorView.lineWrapping,
        placeholder('Site styling CSS'),
        EditorView.updateListener.of((u) => {
          if (u.docChanged && !cssSyncing) {
            customCss.value = cssView.state.doc.toString()
            onCustomCssInput()
          }
        }),
      ],
    }),
    parent: cssEl.value,
  })
  addEventListener('keydown', onKeydown)
  await loadSettings()
  parseFonts(customCss.value)
  setCssDocument(customCss.value)
  applyCustomCss(customCss.value)
})

onUnmounted(() => {
  clearTimeout(reconnectTimer)
  clearTimeout(armTimer)
  for (const t of Object.values(timers)) clearTimeout(t)
  if (ws) {
    ws.onclose = null // intentional close, no reconnect
    ws.close()
  }
  view?.destroy()
  cssView?.destroy()
  removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="editor-root overlay">
    <header class="toolbar">
      <span class="mode-label">site editor</span>
      <button type="button" class="close" title="close" @click="close">✕</button>
    </header>
    <div v-if="saveError">{{ saveError }}</div>

    <section class="block">
      <label class="field">
        <span class="field-label">site</span>
        <input
          v-model="brand"
          class="text-input"
          placeholder="Site name (header link and window title)"
          @input="onBrandInput"
        />
        <select
          v-model="theme"
          class="text-input theme-select"
          title="Theme"
          @change="onThemeChange"
        >
          <option v-for="opt in themeOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
        <button
          type="button"
          class="font-btn"
          :class="{ active: !!fontPicker }"
          title="Fonts"
          @click="toggleFontPanel"
        >
          A
        </button>
      </label>
      <div class="favicon-row">
        <img v-if="favicon" :src="favicon" class="favicon-preview" alt="" />
        <span v-else class="favicon-preview favicon-empty">?</span>
        <button
          type="button"
          title="upload favicon (ico, png, svg...)"
          @click="faviconInput.click()"
        >{{ favicon ? 'replace favicon' : 'upload favicon' }}</button>
        <button
          v-if="favicon"
          type="button"
          title="remove favicon (back to the default)"
          @click="removeFavicon"
        >remove</button>
        <input
          ref="faviconInput"
          type="file"
          accept="image/*"
          hidden
          @change="(ev) => { uploadFavicon(ev.target.files[0]); ev.target.value = '' }"
        />
      </div>
      <div v-if="fontPicker" class="font-picker">
          <div class="font-tabs">
            <button
              v-for="name in ['body', 'heading', 'brand']"
              :key="name"
              type="button"
              :class="{ active: fontPicker === name }"
              @click="fontPicker = name"
            >
              {{ name }}
            </button>
          </div>
          <div class="font-cols">
            <div class="font-col">
              <button
                v-for="opt in serifFonts"
                :key="opt.label"
                type="button"
                class="font-opt"
                :class="{ current: fontRefFor(fontPicker).value === opt.value }"
                :style="{ fontFamily: opt.value, ...fontStyleFor(fontPicker) }"
                @click="pickFont(opt.value)"
              >
                {{ opt.label }}
              </button>
            </div>
            <div class="font-col">
              <button
                v-for="opt in sansFonts"
                :key="opt.label"
                type="button"
                class="font-opt"
                :class="{ current: fontRefFor(fontPicker).value === opt.value }"
                :style="{ fontFamily: opt.value, ...fontStyleFor(fontPicker) }"
                @click="pickFont(opt.value)"
              >
                {{ opt.label }}
              </button>
            </div>
          </div>
        </div>
      <div ref="cssEl" class="css-cm" />
    </section>

    <section class="block" @paste="onBannerPaste">
      <div class="block-head">
        <span class="field-label">Banner on /{{ path }}</span>
        <select
          v-model="bannerDesign"
          class="text-input design-select"
          title="Banner design (artwork + its own styles)"
          @change="onBannerDesignChange"
        >
          <option :value="null">{{ inheritLabel }}</option>
          <option value="">none</option>
          <option v-for="d in bannerDesigns" :key="d" :value="d">{{ d }}</option>
        </select>
        <button
          type="button"
          title="upload banner image/video (replaces existing media) — pasting works too"
          @click="fileInput.click()"
        >add image/video</button>
        <input
          ref="fileInput"
          type="file"
          accept="image/*,video/*"
          hidden
          @change="(ev) => { uploadBannerMedia(ev.target.files[0]); ev.target.value = '' }"
        />
      </div>
      <div ref="bannerEl" class="banner-cm" />
    </section>

    <section class="block structure">
      <StructureTree :nodes="tree" />
    </section>
  </div>
</template>

<style scoped>
.editor-root {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

/* Docked-overlay positioning lives in the global pagerite.css (.editor-host /
   .editor-root.overlay) since the host element is created by main.js. */

.toolbar {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.mode-label {
  color: var(--muted);
  font-size: 0.85rem;
  white-space: nowrap;
}

/* Window-style close button, top right corner. */
.toolbar .close {
  margin-left: auto;
  padding: 0 0.3rem;
  background: none;
  border: none;
  color: var(--muted);
  font-size: 1.05rem;
  cursor: pointer;
}

.toolbar .close:hover {
  color: var(--text);
}

.block {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.block-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.field {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.field-label {
  color: var(--muted);
  font-size: 0.85rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Favicon row: tiny preview (or placeholder tile) + upload/remove buttons. */
.favicon-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.favicon-preview {
  width: 1.4rem;
  height: 1.4rem;
  object-fit: contain;
  border-radius: 3px;
  background: var(--bg);
  border: 1px solid var(--line);
}

.favicon-empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  font-size: 0.85rem;
}

.favicon-row button {
  font: inherit;
  font-size: 0.85rem;
  padding: 0.15rem 0.6rem;
  background: var(--accent2);
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
}

.block-head button {
  margin-left: auto;
  font: inherit;
  font-size: 0.85rem;
  padding: 0.15rem 0.6rem;
  background: var(--accent2);
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
}

/* The banner design selector sits between the label and the upload button
   (which stays pushed right by its auto margin). */
.design-select {
  flex: 0 1 auto;
  width: auto;
  font-size: 0.85rem;
}

.text-input {
  flex: 1;
  min-width: 4rem;
  font: inherit;
  font-size: 0.9rem;
  padding: 0.2rem 0.5rem;
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: 4px;
}

.theme-select {
  flex: 0 0 auto;
  width: auto;
}

/* Stylized "A" icon button opening the font panel — always Fira Code, so
   it stays distinctive no matter which fonts are configured. */
.font-btn {
  font-family: var(--font-fira-code);
  font-weight: 700;
  font-size: 1.5rem;
  padding: 0 0.4rem;
  color: var(--muted);
  background: none;
  border: none;
  cursor: pointer;
}

.font-btn:hover {
  color: var(--text);
}

.font-btn.active {
  color: var(--accent);
}

/* Font picker panel. Colors come from the theme variables, so contrast
   against the page background is automatic in both light and dark themes. */
.font-picker {
  padding: 0.5rem;
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: 6px;
  box-shadow: 0 4px 16px #0004;
}

.font-tabs {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 0.4rem;
}

.font-tabs button {
  flex: 1;
  padding: 0.15rem 0.5rem;
  font: inherit;
  color: var(--muted);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
}

.font-tabs button.active {
  color: var(--text);
  border-bottom-color: var(--accent);
}

.font-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.25rem 0.75rem;
}

.font-col {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

/* Each option's name is its own preview (font family + size/weight of the
   element being styled are set inline). */
.font-opt {
  padding: 0.3rem 0.45rem;
  line-height: 1.3;
  color: inherit;
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
  text-align: left;
}

.font-opt:hover {
  background: var(--surface);
}

.font-opt.current {
  border-color: var(--accent);
}

/* Small CodeMirror window for the banner HTML; scrolls internally. */
.banner-cm {
  border: 1px solid var(--line);
  border-radius: 4px;
  overflow: hidden;
}

.banner-cm :deep(.cm-editor) {
  max-height: 7rem;
  font-size: 0.85rem;
}

.banner-cm :deep(.cm-scroller) {
  overflow: auto;
}

/* No line numbers / gutter chrome. */
.banner-cm :deep(.cm-gutters) {
  display: none;
}

/* CodeMirror window for site-wide custom CSS. */
.css-cm {
  border: 1px solid var(--line);
  border-radius: 4px;
  overflow: hidden;
}

.css-cm :deep(.cm-editor) {
  max-height: 12rem;
  font-size: 0.85rem;
}

.css-cm :deep(.cm-scroller) {
  overflow: auto;
}

.css-cm :deep(.cm-gutters) {
  display: none;
}

.structure {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
</style>
