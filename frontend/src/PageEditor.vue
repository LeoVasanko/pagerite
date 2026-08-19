<script setup>
// Page editor: CodeMirror for Markdown, live server-rendered preview
// applied straight into the visible article, saving over one WebSocket
// (/_api/ws/editor). Docked left of the article on the page itself.
// The socket connects when the editor is opened and reconnects with
// exponential backoff after a failure; unsaved text and pending saves
// survive a disconnect. Editor scroll drives the article scroll (while
// editing the window scroll is locked and only #main scrolls), keeping the
// rendered article at the cursor's position. Saving (💾 / Ctrl+S) is explicit
// and refreshes the page regions in place — never a reload — so the editor
// state (unsaved text included) also survives closing the shell; it is lost
// only on a real page reload.
import { onActivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { markdown } from '@codemirror/lang-markdown'
import { cmHighlight, cmTheme } from './cmtheme'
import { loadPlain } from './swapdoc'

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
  send({ type: 'render', path: path.value, markdown: view.state.doc.toString() })
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
  // (never a reload: the editor keeps its state).
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

function openPath(p) {
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

function previewIntoArticle(html, hasH1) {
  const article = document.querySelector('#main article')
  if (!article) return
  const h1 = article.querySelector('h1')
  const body = article.querySelector('.body')
  // The edit pen may be tucked inside an h1 (title or markdown-owned);
  // detach it before textContent/innerHTML wipes destroy the element.
  // pagerite.js re-places it into the first visible h1 on pagerite:preview.
  const pen = article.querySelector('button.edit-link')
  if (pen && (h1?.contains(pen) || body?.contains(pen))) article.prepend(pen)
  if (h1) {
    h1.style.display = hasH1 ? 'none' : ''
    h1.textContent = title.value
  }
  if (body) {
    body.innerHTML = html
    runScripts(body)
    dispatchEvent(new CustomEvent('pagerite:preview'))
  }
}

function onMessage(ev) {
  const msg = JSON.parse(ev.data)
  if (msg.type === 'doc' && msg.path === path.value) {
    title.value = msg.title
    published.value = msg.published
    setDocument(msg.markdown)
    requestRender()
    dirty.value = false // just loaded from the server, nothing unsaved
  } else if (msg.type === 'html' && msg.path === path.value) {
    previewIntoArticle(msg.html, msg.has_h1)
  } else if (msg.type === 'saved') {
    saveError.value = ''
    pendingSave = null
    dirty.value = false
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
  if (dirty.value) requestRender()
}

function syncScroll() {
  // Editor scroll drives the article: keep the rendered page at the same
  // proportional position as the cursor area in the editor. While editing
  // the window scroll is locked and #main is the scrolling element.
  if (syncingScroll || !view) return
  const main = document.getElementById('main')
  if (!main) return
  syncingScroll = true
  requestAnimationFrame(() => {
    const scroller = view.scrollDOM
    const max = scroller.scrollHeight - scroller.clientHeight
    const pct = max > 0 ? scroller.scrollTop / max : 0
    main.scrollTop = pct * (main.scrollHeight - main.clientHeight)
    syncingScroll = false
  })
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
        EditorView.updateListener.of((u) => { if (u.docChanged) requestRender() }),
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
  view.scrollDOM.addEventListener('scroll', syncScroll)
  // Opening the editor means you want to write: start focused.
  view.focus()
  window.__pageritePageEditor = {
    getMarkdown: () => view.state.doc.toString(),
    setMarkdown: (text) => setDocument(text, true),
    path: () => path.value,
  }
  addEventListener('keydown', onKeydown)
  addEventListener('pagerite:editor-shown', onEditorShown)
})

onUnmounted(() => {
  clearTimeout(reconnectTimer)
  if (ws) {
    ws.onclose = null // intentional close, no reconnect
    ws.close()
  }
  view?.destroy()
  delete window.__pageritePageEditor
  removeEventListener('keydown', onKeydown)
  removeEventListener('pagerite:editor-shown', onEditorShown)
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
   a slight margin to the panel edges. Wheel scroll stays in the editor and
   drives the article (syncScroll) instead of double-scrolling. */
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
