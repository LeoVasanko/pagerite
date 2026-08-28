<script setup>
// Page editor: CodeMirror for Markdown, live server-rendered preview
// applied straight into the visible article, saving over one WebSocket
// (/_api/ws/editor). Docked left of the article on the page itself.
// The socket connects when the editor is opened and reconnects with
// exponential backoff after a failure; unsaved text and pending saves
// survive a disconnect. Editor and article (window) scrolls are linked
// piecewise-linearly both ways, keyed on the section anchors' data-line
// (syncWindowToEditor / syncEditorToWindow).
// Saving (💾 / Ctrl+S) is explicit
// and refreshes the page regions in place — never a reload — so the editor
// state (unsaved text included) also survives closing the shell. The editor
// always follows the URL: navigating away retargets it to the new page,
// stashing unsaved text per path (unsavedStash) so returning to the page
// restores the working draft; stashes clear on save and on real reload.
import { onActivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { markdown } from '@codemirror/lang-markdown'
import { cmHighlight, cmTheme } from './cmtheme'
import { dropPageCache, loadPlain } from './swapdoc'

const props = defineProps({
  pagePath: { type: String, default: '' },
})
const emit = defineEmits(['close', 'pathChange'])

const path = ref('')
const title = ref('')
const published = ref(true)
const saveError = ref('')
const editorEl = ref(null)
const fileInput = ref(null)

let ws = null
let view = null
let savedResolve = null
let pendingSave = null
let reconnectTimer = null
let reconnectDelay = 2000
const MAX_RECONNECT_DELAY = 16000
let everConnected = false
const dirty = ref(false)  // unsaved text exists (drives the 💾 button)
let syncingScroll = false

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

function normPath(p) {
  return p.trim().replace(/^\/+|\/+$/g, '')
}

function pageLabel() {
  return title.value.trim() || ('/' + (path.value || ''))
}

// Show the article title (or path for a new page) plus the edit pen in the
// window title while editing; the server-rendered public title is restored on
// close.
function updateWindowTitle() {
  document.title = `${pageLabel()} 🖊️`
}

watch([title, path], updateWindowTitle)
watch(() => props.pagePath, (p) => { openPath(normPath(p)) })

onActivated(() => {
  updateWindowTitle()
  // Re-shown with unsaved text: restore the working preview into the
  // article (closing discarded it in favour of the server-rendered page).
  if (dirty.value) requestRender()
})

function requestRender() {
  // No debounce: server-side rendering is fast enough per keystroke.
  if (!view) return
  dirty.value = true
  send({ type: 'render', path: path.value, title: title.value, markdown: view.state.doc.toString() })
}

function save() {
  // Path is not editable here (that's the structure tab's job); saving
  // never moves the page.
  const markdown = view.state.doc.toString()
  if (markdown.trim() === '') {
    // Empty text means delete — an explicit choice made here, in the page
    // editor; the save APIs (REST PUT / WS save) never delete on empty.
    return fetch(`/_api/pages/${path.value}`, { method: 'DELETE' }).then((res) => {
      saveError.value = res.ok ? '' : '⚠️ changes could not be saved'
      if (res.ok) unsavedStash.delete(path.value)
    })
  }
  const msg = {
    type: 'save',
    path: path.value,
    title: title.value,
    markdown,
    published: published.value,
  }
  pendingSave = msg
  send(msg)
  return new Promise((resolve) => { savedResolve = resolve })
}

async function saveAndRefresh() {
  await save()
  dirty.value = false
  // Refresh the page regions from the server so nav/sidebar changes apply
  // (never a reload: the editor keeps its state). Drop the prefetch cache
  // first: heading/title changes affect navigation on every page.
  dropPageCache()
  loadPlain(path.value)
}

function close() {
  emit('close')
  // Discard the unsaved preview by re-rendering the page from the server.
  // The editor text itself is kept (the shell stays mounted while hidden)
  // and can still be saved later.
  if (dirty.value) loadPlain(path.value)
}

function insertAtCursor(text) {
  view.dispatch(view.state.replaceSelection(text))
  view.focus()
}

async function uploadImage(file) {
  if (!file) return
  const name = file.name.replace(/[^\w.-]/g, '-')
  const res = await fetch(`/_api/files/${encodeURIComponent(name)}`, { method: 'PUT', body: file })
  if (res.ok) {
    const { path: stored } = await res.json()
    const alt = name.replace(/\.[^.]+$/, '')
    insertAtCursor(`![${alt}](${stored})`)
  }
}

// --- Format toolbar --------------------------------------------------------
// Small Markdown helpers for the hard-to-remember syntax; each leaves the
// relevant part selected so typing replaces it.
function wrapInline(mark) {
  // Toggle: wrapped selection (or wrapping marks around it) is unwrapped.
  const { from, to } = view.state.selection.main
  const doc = view.state.doc
  if (doc.sliceString(Math.max(0, from - mark.length), from) === mark
      && doc.sliceString(to, to + mark.length) === mark) {
    view.dispatch({ changes: [
      { from: to, to: to + mark.length },
      { from: from - mark.length, to: from },
    ] })
  } else {
    const text = doc.sliceString(from, to)
    view.dispatch({
      changes: { from, to, insert: mark + text + mark },
      selection: { anchor: from + mark.length, head: to + mark.length },
    })
  }
  view.focus()
}

function insertCode() {
  // On an empty line with no selection: a fenced code block, cursor inside.
  // Otherwise an inline code wrap (toggling).
  const { from, to } = view.state.selection.main
  const line = view.state.doc.lineAt(from)
  if (from === to && !line.text.trim()) {
    view.dispatch({
      changes: { from: line.from, to: line.to, insert: '```\n\n```' },
      selection: { anchor: line.from + 4 },
    })
    view.focus()
    return
  }
  wrapInline('`')
}

function insertLink() {
  // Selected text becomes the link label — or the URL if it looks like one.
  const { from, to } = view.state.selection.main
  const text = view.state.sliceDoc(from, to)
  const isUrl = /^https?:\/\/\S+$/.test(text)
  const insert = isUrl ? `[](${text})` : `[${text}]()`
  const urlStart = from + insert.length - 1 // inside the parens
  view.dispatch({
    changes: { from, to, insert },
    selection: isUrl ? { anchor: from + 1 } : { anchor: urlStart },
  })
  view.focus()
}

// Table size picker: a hover grid popup (cols × rows) under the toolbar.
const tablePicker = ref(false)
const tableSize = ref({ cols: 0, rows: 0 })
const TABLE_MAX_COLS = 8
const TABLE_MAX_ROWS = 6

function insertTable(cols, rows) {
  // A GFM table on its own blank-separated block, first header cell
  // selected.
  const { from, to } = view.state.selection.main
  const before = from > 0 && view.state.doc.sliceString(from - 1, from) !== '\n' ? '\n\n' : ''
  const row = (cells) => `| ${cells.join(' | ')} |`
  const table = `${before}${row(Array(cols).fill('column'))}\n`
    + `${row(Array(cols).fill('---'))}\n`
    + `${Array(rows).fill(row(Array(cols).fill(''))).join('\n')}\n`
  view.dispatch({
    changes: { from, to, insert: table },
    selection: { anchor: from + before.length + 2, head: from + before.length + 8 },
  })
  tablePicker.value = false
  view.focus()
}

// Unsaved edits survive navigation within the session: leaving a page
// stashes its working text here, returning restores it (the server doc
// still arrives, for title/published and as the base underneath).
// Entries clear on save and on real reload (the shell is in-memory only).
const unsavedStash = new Map()

function openPath(p) {
  if (dirty.value && path.value && p !== path.value) {
    unsavedStash.set(path.value, view.state.doc.toString())
  }
  path.value = p
  send({ type: 'open', path: p })
}

function setDocument(text, preserveSelection = false) {
  const tr = { changes: { from: 0, to: view.state.doc.length, insert: text } }
  if (preserveSelection) tr.selection = view.state.selection
  view.dispatch(tr)
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

function previewIntoArticle(html, multicol) {
  const article = document.querySelector('#main article')
  if (!article) return
  // The server render owns the article completely — the injected title h1,
  // the column layout (.multicol on the article, the .colseg/.cols
  // segments) — so the whole article content swaps as one. Only the edit
  // pen and the category cards survive: detach them before innerHTML wipes
  // them. pagerite.js re-places the pen into the first visible h1 on
  // pagerite:preview.
  article.classList.toggle('multicol', multicol)
  const pen = article.querySelector('button.edit-link')
  if (pen) pen.remove()
  const cards = article.querySelector(':scope > .cards')
  if (cards) cards.remove()
  article.innerHTML = html
  if (cards) article.append(cards)
  runScripts(article)
  dispatchEvent(new CustomEvent('pagerite:preview'))
}

function onMessage(ev) {
  const msg = JSON.parse(ev.data)
  if (msg.type === 'doc' && msg.path === path.value) {
    title.value = msg.title
    published.value = msg.published
    // Restore stashed unsaved edits over the server doc when returning
    // to a page left dirty.
    const stashed = unsavedStash.get(msg.path)
    setDocument(stashed ?? msg.markdown)
    dirty.value = stashed != null
    requestRender()
    // A section pen's target line survives the open/path-switch here.
    consumePendingLine()
  } else if (msg.type === 'html' && msg.path === path.value) {
    previewIntoArticle(msg.html, msg.multicol)
  } else if (msg.type === 'saved') {
    saveError.value = ''
    pendingSave = null
    dirty.value = false
    unsavedStash.delete(path.value)
    savedResolve?.()
    savedResolve = null
  } else if (msg.type === 'error') {
    saveError.value = '⚠️ changes could not be saved'
  }
}

function onKeydown(ev) {
  if (!(ev.ctrlKey || ev.metaKey)) return
  if (ev.key === 's') {
    ev.preventDefault()
    save()
    return
  }
  // Bold/italic only when typing in the CodeMirror editor itself.
  if (!view?.hasFocus) return
  if (ev.key === 'b') {
    ev.preventDefault()
    wrapInline('**')
  } else if (ev.key === 'i') {
    ev.preventDefault()
    wrapInline('*')
  }
}

// The shell stays mounted while hidden: when it is re-shown with this tab
// active, restore the window title (and the working preview if unsaved
// text exists — closing discarded it in favour of the server render).
function onEditorShown() {
  if (document.body.dataset.editorMode !== 'page') return
  updateWindowTitle()
  // Always follow the URL: if the user navigated while the editor was
  // hidden or on another tab, retarget (discarding unsaved text — its
  // preview page is gone); otherwise restore the working preview.
  const p = normPath(props.pagePath)
  if (p !== path.value) openPath(p)
  else if (dirty.value) requestRender()
  consumePendingLine()
}

// Piecewise-linear scroll sync between the CodeMirror scroller and the
// window (the article's scroller, also while editing), keyed on the
// section anchors: the backend tags anchored h1/h2 headings with
// data-line (markdown source line), so each heading pairs a document
// position with a page position, and positions interpolate linearly
// between neighbouring headings. Endpoints are the article top (line 1)
// and the document bottom (last line).
//
// Editor → page follows the CURSOR, not the editor viewport: the cursor's
// fractional line (soft-wrap included, so moving inside a wrapped
// paragraph tracks smoothly) maps to its page position, shown at a fixed
// anchor height in the window — the cursor on the last line lands at the
// end of the page, no ramping needed. Only cursor/selection changes drive
// this direction: editor wheel-scrolling repositions the text, not the
// page, which removes the scroll→scroll echo entirely.
// Page → editor anchors a viewport fraction that grows with page progress
// (0 = heading at viewport top when the page is at the top, 1 = viewport
// bottom at the page's end), so both document ends line up exactly.
//
// Both directions apply instantly (never smooth — a smooth window scroll
// feeds its intermediate positions back into the editor and fights the
// user's scrolling) and coalesce to one update per frame. Loops are
// broken two ways: a driver flag held until one frame AFTER the write
// (the scroll event a programmatic write dispatches arrives
// asynchronously — clearing the flag in the writing frame would let the
// echo through and the two directions would chase each other, which
// showed up as random jumping whenever layout shifted the targets
// mid-scroll), and a 1px tolerance so residual rounding is a no-op. When
// the panel's height changes mid-scroll (its top tracks the banner), the
// page is the driver: the editor is re-matched to the page's position,
// never vice versa.

// [markdown line (1-based), window Y] control points, ascending in both.
function syncPoints() {
  const article = document.querySelector('#main article')
  if (!article || !view) return null
  const pts = [[1, article.getBoundingClientRect().top + scrollY]]
  for (const h of article.querySelectorAll('[data-line]')) {
    pts.push([+h.dataset.line + 1, h.getBoundingClientRect().top + scrollY])
  }
  pts.push([view.state.doc.lines, document.documentElement.scrollHeight])
  return pts.sort((a, b) => a[0] - b[0])
}

// Piecewise-linear map of v from column `from` to column `to`, clamped to
// the segment ends.
function interp(pts, v, from, to) {
  let i = 1
  while (i < pts.length - 1 && pts[i][from] < v) i++
  const [a0, b0] = [pts[i - 1][from], pts[i - 1][to]]
  const [a1, b1] = [pts[i][from], pts[i][to]]
  const t = a1 > a0 ? (v - a0) / (a1 - a0) : 0
  return b0 + Math.max(0, Math.min(1, t)) * (b1 - b0)
}

// Editor scroller top showing the fractional markdown line.
function editorTopFor(line) {
  const scroller = view.scrollDOM
  const max = Math.max(0, scroller.scrollHeight - scroller.clientHeight)
  if (line >= view.state.doc.lines) return max
  const n = Math.max(1, Math.floor(line))
  const block = view.lineBlockAt(view.state.doc.line(n).from)
  return Math.min(max, block.top + (line - n) * block.height)
}

//: Window height fraction where the cursor's page position is shown.
const CURSOR_ANCHOR = 1 / 3

function syncWindowToEditor() {
  if (syncingScroll || !view) return
  syncingScroll = true
  requestAnimationFrame(() => {
    const pts = syncPoints()
    if (pts) {
      // The cursor's page position, shown at a fixed window height.
      const pos = view.state.selection.main.head
      const coords = view.coordsAtPos(pos)
      if (coords) {
        const scroller = view.scrollDOM
        const block = view.lineBlockAt(pos)
        const docY = coords.top - scroller.getBoundingClientRect().top + scroller.scrollTop
        const frac = block.height > 0
          ? Math.max(0, Math.min(1, (docY - block.top) / block.height))
          : 0
        const line = view.state.doc.lineAt(pos).number + frac
        const y = interp(pts, line, 0, 1) - CURSOR_ANCHOR * innerHeight
        if (Math.abs(scrollY - y) > 1) scrollTo({ top: Math.max(0, y), behavior: 'instant' })
      }
    }
    requestAnimationFrame(() => { syncingScroll = false })
  })
}

function syncEditorToWindow() {
  if (syncingScroll || !view) return
  syncingScroll = true
  requestAnimationFrame(() => {
    const pts = syncPoints()
    if (pts) {
      // Anchor fraction grows with page progress: the mapped line sits at
      // the viewport top when the page is at its top, at the bottom when
      // scrolled all the way down.
      const pageMax = Math.max(0, document.documentElement.scrollHeight - innerHeight)
      const a = pageMax > 0 ? scrollY / pageMax : 0
      const line = interp(pts, scrollY + a * innerHeight, 1, 0)
      const scroller = view.scrollDOM
      const top = editorTopFor(line) - a * scroller.clientHeight
      const max = Math.max(0, scroller.scrollHeight - scroller.clientHeight)
      const clamped = Math.max(0, Math.min(max, top))
      if (Math.abs(scroller.scrollTop - clamped) > 1) scroller.scrollTop = clamped
    }
    requestAnimationFrame(() => { syncingScroll = false })
  })
}

// Jump both views to a markdown source line (0-based, as carried by the
// section pens' data-line / window.__pageriteEditLine).
function scrollToSourceLine(line) {
  if (!view || line == null) return
  const n = Math.max(1, Math.min(line + 1, view.state.doc.lines))
  const pos = view.state.doc.line(n).from
  view.dispatch({
    selection: { anchor: pos },
    effects: EditorView.scrollIntoView(pos, { y: 'start', yMargin: 8 }),
  })
  const h = document.querySelector(`#main article [data-line="${line}"]`)
  if (h) scrollTo({ top: h.getBoundingClientRect().top + scrollY, behavior: 'instant' })
}

// A section pen carries its line in window.__pageriteEditLine; consume it
// once the document is here (fresh open, path switch, re-shown shell).
function consumePendingLine() {
  const line = window.__pageriteEditLine
  if (line == null) return
  delete window.__pageriteEditLine
  scrollToSourceLine(line)
}

function connect() {
  ws = new WebSocket(
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/_api/ws/editor`,
  )
  ws.onmessage = onMessage
  ws.onopen = () => {
    reconnectDelay = 2000
    if (everConnected) {
      // Reconnected: local text is authoritative — don't re-open (that
      // would clobber the editor), just resync preview and pending saves.
      requestRender()
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
  connect()
  updateWindowTitle()

  view = new EditorView({
    state: EditorState.create({
      doc: '',
      extensions: [
        basicSetup,
        markdown(),
        cmTheme,
        cmHighlight,
        EditorView.lineWrapping, // Markdown lines are long: soft-wrap them
        EditorView.updateListener.of((u) => {
          if (u.docChanged) requestRender()
          // Cursor moves (typing included) drive the page scroll sync.
          if (u.selectionSet) syncWindowToEditor()
        }),
        EditorView.domEventHandlers({
          paste(ev) {
            // Paste an image straight into the article: upload + insert
            const file = [...(ev.clipboardData?.files || [])]
              .find((f) => f.type.startsWith('image/'))
            if (file) {
              ev.preventDefault()
              uploadImage(file)
            }
          },
        }),
      ],
    }),
    parent: editorEl.value,
  })
  // Page → editor: window scroll (and resizes, e.g. the panel growing when
  // the banner scrolls away) re-match the editor to the page's position.
  // The other direction is cursor-driven (updateListener above), never
  // scroll-driven — an editor scroll moves text, not the page.
  addEventListener('scroll', syncEditorToWindow, { passive: true })
  addEventListener('resize', syncEditorToWindow)
  // Opening the editor means you want to write: start focused.
  view.focus()
  window.__pageritePageEditor = {
    getMarkdown: () => view.state.doc.toString(),
    setMarkdown: (text) => setDocument(text, true),
    path: () => path.value,
  }
  addEventListener('keydown', onKeydown)
  addEventListener('pagerite:editor-shown', onEditorShown)
  // A section pen clicked while the page editor is already open.
  addEventListener('pagerite:edit-section', consumePendingLine)
})

onUnmounted(() => {
  clearTimeout(reconnectTimer)
  if (ws) {
    ws.onclose = null // intentional close, no reconnect
    ws.close()
  }
  view?.destroy()
  delete window.__pageritePageEditor
  removeEventListener('scroll', syncEditorToWindow)
  removeEventListener('resize', syncEditorToWindow)
  removeEventListener('keydown', onKeydown)
  removeEventListener('pagerite:editor-shown', onEditorShown)
  removeEventListener('pagerite:edit-section', consumePendingLine)
})
</script>

<template>
  <div class="page-editor">
    <header class="toolbar">
      <label class="title-field">
        <span class="field-label">title</span>
        <input v-model="title" class="title" @input="requestRender" />
      </label>
      <label><input v-model="published" type="checkbox" /> published</label>
      <input
        ref="fileInput"
        type="file"
        accept="image/*"
        hidden
        @change="(ev) => { uploadImage(ev.target.files[0]); ev.target.value = '' }"
      />
      <button
        type="button"
        class="save"
        title="save (Ctrl+S)"
        :disabled="!dirty"
        @click="saveAndRefresh"
      >💾</button>
    </header>
    <div class="format-bar">
      <button type="button" title="bold" @click="wrapInline('**')"><b>B</b></button>
      <button type="button" title="italic" @click="wrapInline('*')"><i>I</i></button>
      <button type="button" title="code (empty line: code block)" @click="insertCode"><code>&lt;/&gt;</code></button>
      <button type="button" title="link" @click="insertLink">🔗</button>
      <button
        type="button"
        title="table"
        :class="{ active: tablePicker }"
        @click="tablePicker = !tablePicker"
      >▦</button>
      <button type="button" title="insert image (upload) — pasting works too" @click="fileInput.click()">🖼️</button>
      <div v-if="tablePicker" class="table-picker" @mouseleave="tableSize = { cols: 0, rows: 0 }">
        <div class="tp-grid" :style="{ gridTemplateColumns: `repeat(${TABLE_MAX_COLS}, 1fr)` }">
          <button
            v-for="n in TABLE_MAX_COLS * TABLE_MAX_ROWS"
            :key="n"
            type="button"
            class="tp-cell"
            :class="{ on: tableSize.cols >= (n - 1) % TABLE_MAX_COLS + 1 && tableSize.rows >= Math.floor((n - 1) / TABLE_MAX_COLS) + 1 }"
            @mouseenter="tableSize = { cols: (n - 1) % TABLE_MAX_COLS + 1, rows: Math.floor((n - 1) / TABLE_MAX_COLS) + 1 }"
            @click="insertTable(tableSize.cols, tableSize.rows)"
          />
        </div>
        <div class="tp-size">{{ tableSize.cols || '–' }} × {{ tableSize.rows || '–' }}</div>
      </div>
    </div>
    <div v-if="saveError">{{ saveError }}</div>
    <div class="panes">
      <div ref="editorEl" class="editor" />
    </div>
  </div>
</template>

<style scoped>
.page-editor {
  display: flex;
  flex-direction: column;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.toolbar .title-field {
  flex: 1;
  min-width: 4rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.toolbar .field-label {
  color: var(--muted);
  font-size: 0.85rem;
}

.toolbar .title {
  flex: 1;
  min-width: 4rem;
  font: inherit;
  padding: 0.2rem 0.5rem;
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: 4px;
}

.toolbar label {
  color: var(--muted);
  font-size: 0.85rem;
  white-space: nowrap;
}

/* Borderless icon button; greyed out (desaturated) while there is nothing
   to save. */
.toolbar button.save {
  padding: 0 0.3rem;
  font-size: 1rem;
  background: none;
  border: none;
  color: var(--text);
  cursor: pointer;
}

/* Disabled = black & white only; an explicit color overrides the browser's
   built-in dimmed disabled-button color. */
.toolbar button.save:disabled {
  color: var(--text);
  filter: saturate(0);
  cursor: default;
}

/* Markdown helpers: plain icon buttons under the toolbar. */
.format-bar {
  position: relative;
  display: flex;
  align-items: center;
  gap: 0.15rem;
  padding: 0.25rem 1rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.format-bar button {
  min-width: 1.7rem;
  padding: 0.15rem 0.3rem;
  font: inherit;
  font-size: 0.85rem;
  color: var(--muted);
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.format-bar button:hover,
.format-bar button.active {
  color: var(--text);
  border-color: var(--line);
}

/* Table size picker: hover grid popup below the format bar; the hovered
   cell and everything up-left of it is the table to insert. */
.table-picker {
  position: absolute;
  top: 100%;
  left: 6.5rem;
  z-index: 20;
  padding: 0.5rem;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: 6px;
  box-shadow: 0 4px 16px #0004;
}

.tp-grid {
  display: grid;
  gap: 2px;
}

.tp-cell {
  width: 1.05rem;
  height: 1.05rem;
  min-width: 0;
  padding: 0;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 2px;
}

.tp-cell.on {
  background: var(--accent);
  border-color: var(--accent);
}

.tp-size {
  margin-top: 0.35rem;
  color: var(--muted);
  font-size: 0.8rem;
  text-align: center;
}

.panes {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 0.6rem 1rem;
  gap: 1rem;
  background: var(--surface); /* dialog body, same as the toolbar */
}

/* CodeMirror sits inside a bordered box, like a dialog's input area, with
   a slight margin to the panel edges. Wheel scroll stays in the editor
   (overscroll-behavior) instead of double-scrolling the page. */
.editor {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  overscroll-behavior: contain;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--bg);
}

.editor :deep(.cm-editor) {
  height: 100%;
}

/* No line numbers / gutter chrome. */
.editor :deep(.cm-gutters) {
  display: none;
}
</style>
