<script setup>
// Site editor: site-wide brand, banner HTML for the current page
// (previewed into the real #page-banner region, so you see exactly which
// banner you're editing) and the draggable site structure tree. Opened
// from the pen on the banner. Everything saves immediately as you edit —
// no save button, no edit mode. Focusing a page's row navigates to it in
// place (no transitions).
//
// The tree comes from the server nested (GET /_/api/pages); every node is
// real — a label with a title and slug, with content (landing page) or
// without (category redirecting to its first child). The front page is a
// top-level row with an empty slug, not the parent of the others.
import { computed, onMounted, onUnmounted, provide, ref } from 'vue'
import StructureTree from './StructureTree.vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState, Compartment } from '@codemirror/state'
import { placeholder } from '@codemirror/view'
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
  for (const id of ['page-banner', 'nav', 'sidebar', 'main']) {
    const fresh = doc.getElementById(id)
    const el = document.getElementById(id)
    if (fresh && el) el.replaceWith(document.importNode(fresh, true))
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
  document.title = doc.title
}

async function loadPlain(p) {
  let doc
  let finalUrl = `/${p}`
  try {
    const res = await fetch(finalUrl)
    const type = res.headers.get('content-type') || ''
    if (!type.includes('text/html')) return
    // Category URLs redirect to their first child; reflect that. A 404
    // layout is fine too (new pages are created by editing them).
    if (res.redirected) finalUrl = res.url
    doc = new DOMParser().parseFromString(await res.text(), 'text/html')
  } catch { return }
  if (!doc.getElementById('main')) return
  swapRegions(doc)
  history.replaceState(null, '', finalUrl)
  runScripts(document.getElementById('page-banner'))
  runScripts(document.getElementById('main'))
  dispatchEvent(new CustomEvent('pagerite:preview')) // re-tuck the edit pen
  // The swap brought in the server-rendered (inherited) banner; overlay
  // the page's own banner if one is being edited.
  if (banner.value.trim()) previewBanner()
}

// Tree row focus: switch the edited page and show it, skipping transitions.
function navigate(p) {
  openPath(p)
  loadPlain(p)
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
  const res = await fetch(`/_/api/pages/${newPath}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      title: node.title.trim() || slug,
      markdown: '',
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
  navigate(newPath)
}

// Give a content-less category a landing page (empty page at its path).
async function addContent(node) {
  const res = await fetch(`/_/api/pages/${node.path}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title: node.title, markdown: '', published: node.published }),
  })
  if (res.ok) {
    saveError.value = ''
    await refreshPages()
    navigate(node.path)
  } else {
    saveError.value = '⚠️ changes could not be saved'
  }
}

// --- Site-wide brand (header link + <title> suffix) ----------------------
// Edits apply to the live page immediately and save while typing. An
// empty brand removes the header link and the title suffix entirely.
const brand = ref('')

async function loadSettings() {
  try {
    brand.value = (await (await fetch('/_/api/settings')).json()).brand
  } catch { /* keep default */ }
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

async function saveBrand() {
  const res = await fetch('/_/api/settings', {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ brand: brand.value }),
  })
  if (res.ok) {
    saveError.value = ''
  } else {
    saveError.value = '⚠️ changes could not be saved'
  }
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
  const res = await fetch(`/_/api/pages/${node.path}`, { method: 'DELETE' })
  if (res.ok) {
    saveError.value = ''
    refreshPages()
    const p = node.path
    if (p === path.value || (p && path.value.startsWith(`${p}/`))) {
      // The current page was deleted — or reduced to a category that now
      // redirects to its first child. Either way, re-render from the server.
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
    tree.value = await (await fetch('/_/api/pages')).json()
  } catch { /* list stays stale; not fatal */ }
}

// Human-readable reason from a failed API call (FastAPI errors carry a
// JSON {detail}), falling back to a generic message.
async function errorDetail(res) {
  const body = await res.json().catch(() => null)
  return body?.detail || 'changes could not be saved'
}

async function postStructure(op) {
  const res = await fetch('/_/api/structure', {
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
  addContent,
  commitPending,
  discardPending,
  newPage,
})

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
    // Own banner: preview it live over the region.
    el.innerHTML = banner.value
    runScripts(el)
  } else {
    // No banner of its own: the region must show the inherited/default
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
  const res = await fetch(`/_/api/files/${encodeURIComponent(name)}`, { method: 'PUT', body: file })
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
    // Placeholder tells where an empty banner falls back to.
    view.dispatch({
      effects: bannerPh.reconfigure(placeholder(
        msg.banner_from == null
          ? 'using default artwork'
          : `inherited from /${msg.banner_from}`,
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
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/_/api/ws/editor`,
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

onMounted(() => {
  refreshPages()
  loadSettings()
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
  addEventListener('keydown', onKeydown)
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
      </label>
    </section>

    <section class="block" @paste="onBannerPaste">
      <div class="block-head">
        <span class="field-label">Banner on /{{ path }}</span>
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

/* Docked-overlay positioning lives in the global style.css (.editor-host /
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

.structure {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
</style>
