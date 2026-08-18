<script setup>
// Page editor: CodeMirror for Markdown, live server-rendered preview
// applied straight into the visible article, saving over one WebSocket
// (/_api/ws/editor). Docked left of the article on the page itself.
// The socket connects when the editor is opened and reconnects with
// exponential backoff after a failure; unsaved text and pending saves
// survive a disconnect. Editor scroll drives the document scroll, keeping the
// rendered article at the cursor's position.
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { markdown } from '@codemirror/lang-markdown'
import { cmHighlight, cmTheme } from './cmtheme'

const props = defineProps({
  pagePath: { type: String, default: '' },
})
const emit = defineEmits(['close'])

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
let dirty = false
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

function requestRender() {
  // No debounce: server-side rendering is fast enough per keystroke.
  if (!view) return
  dirty = true
  send({ type: 'render', path: path.value, markdown: view.state.doc.toString() })
}

function save() {
  // Path is not editable here (that's the site editor's job); saving
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

async function saveAndClose() {
  await save()
  // Reload so nav/sidebar changes apply, then the editor is gone.
  dirty = false
  emit('close')
  location.reload()
}

function close() {
  // Reload if the visible page is showing unsaved preview edits.
  const stale = dirty
  dirty = false
  emit('close')
  if (stale) location.reload()
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
    dirty = false // just loaded from the server, nothing unsaved
  } else if (msg.type === 'html' && msg.path === path.value) {
    previewIntoArticle(msg.html, msg.has_h1)
  } else if (msg.type === 'saved') {
    saveError.value = ''
    pendingSave = null
    savedResolve?.()
    savedResolve = null
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

function syncScroll() {
  // Editor scroll drives the document: keep the rendered article at the
  // same proportional position as the cursor area in the editor.
  if (syncingScroll || !view) return
  syncingScroll = true
  requestAnimationFrame(() => {
    const scroller = view.scrollDOM
    const max = scroller.scrollHeight - scroller.clientHeight
    const pct = max > 0 ? scroller.scrollTop / max : 0
    const doc = document.documentElement
    window.scrollTo(0, pct * (doc.scrollHeight - innerHeight))
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
})
</script>

<template>
  <div class="editor-root overlay">
    <header class="toolbar">
      <input v-model="title" placeholder="Title" class="title" @input="requestRender" />
      <label><input v-model="published" type="checkbox" /> published</label>
      <input
        ref="fileInput"
        type="file"
        accept="image/*"
        hidden
        @change="(ev) => { uploadImage(ev.target.files[0]); ev.target.value = '' }"
      />
      <button type="button" @click="fileInput.click()">image</button>
      <button type="button" @click="saveAndClose">save</button>
      <button type="button" class="close" title="close" @click="close">✕</button>
    </header>
    <div v-if="saveError">{{ saveError }}</div>
    <div class="panes">
      <div ref="editorEl" class="editor" />
    </div>
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

.toolbar button {
  font: inherit;
  padding: 0.25rem 0.8rem;
  background: var(--accent2);
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
}

/* Window-style close button, top right corner. */
.toolbar .close {
  margin-left: auto;
  padding: 0 0.3rem;
  background: none;
  color: var(--muted);
  font-size: 1.05rem;
}

.toolbar .close:hover {
  color: var(--text);
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
   drives the document (syncScroll) instead of double-scrolling. */
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
