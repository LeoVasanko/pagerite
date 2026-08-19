<script setup>
// Banner editor tab: per-page banner HTML and banner design, previewed into
// the real #page-banner region. Close and tab switching live in EditorShell.
import { computed, onActivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { html } from '@codemirror/lang-html'
import { cmHighlight, cmTheme } from './cmtheme'
import { loadPlain, runScripts } from './swapdoc'

const props = defineProps({
  pagePath: { type: String, default: '' },
})
// close/path-change are wired by EditorShell; this tab never emits them.
defineEmits(['close', 'pathChange'])

const path = ref('')
const banner = ref('')
const saveError = ref('')
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

// Banner design selector options come from the backend via settings.
const theme = ref('')
const bannerDesign = ref(null)
const bannerDesignFrom = ref(null)
const bannerDesigns = ref([])
// Where an empty banner code field falls back to (null = nowhere: the
// banner is just the design artwork), shown as a caption under the field.
const bannerFrom = ref(null)
// The design that "inherit" resolves to and where it comes from
// (bannerDesignFrom: an ancestor path, "" = the front page, null = the
// active theme's default).
const bannerDesignInherited = ref('')
// One-shot callback run on the next save ack (set by onBannerDesignChange,
// whose re-render must not race the save it triggers).
let refreshOnSave = null

// The inherit option names the design actually in effect and its source.
const inheritLabel = computed(() => {
  if (bannerDesignFrom.value === null) {
    return `— (from ${theme.value || 'none'})`
  }
  const where = bannerDesignFrom.value === ''
    ? 'the front page'
    : `/${bannerDesignFrom.value}`
  return `— (${bannerDesignInherited.value || 'none'} from ${where})`
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

const timers = {}
function debounce(key, fn, ms = 600) {
  clearTimeout(timers[key])
  timers[key] = setTimeout(fn, ms)
}

function save() {
  const msg = { type: 'save', path: normPath(path.value), banner: banner.value }
  pendingSave = msg
  send(msg)
}

function openPath(p) {
  path.value = p
  send({ type: 'open', path: p })
}
watch(() => props.pagePath, (p) => { openPath(normPath(p)) })

function updateWindowTitle() {
  document.title = `banner: /${path.value} 🖊️`
}

onActivated(() => {
  updateWindowTitle()
  if (banner.value.trim()) previewBanner()
})

// The shell stays mounted while hidden: when it is re-shown with this tab
// active, restore the window title and banner preview.
function onEditorShown() {
  if (document.body.dataset.editorMode !== 'banner') return
  updateWindowTitle()
  if (banner.value.trim()) previewBanner()
}

async function loadSettings() {
  try {
    const s = await (await fetch('/_api/settings')).json()
    theme.value = s.theme || ''
    bannerDesigns.value = s.banner_designs || []
  } catch { /* keep default */ }
}

// Re-render the page from the server (after banner design changes), then
// re-overlay the page's own banner code if one is being edited.
async function rerender() {
  if (await loadPlain(path.value)) {
    if (banner.value.trim()) previewBanner()
  }
}

// --- Banner design ---------------------------------------------------------
function onBannerDesignChange() {
  const msg = {
    type: 'save',
    path: normPath(path.value),
    banner_design: bannerDesign.value,
  }
  pendingSave = msg
  send(msg)
  refreshOnSave = rerender
}

// --- Banner editing ------------------------------------------------------
function setDocument(text) {
  syncing = true
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } })
  syncing = false
  banner.value = text
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
  if (!res.ok) return
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
    bannerDesignInherited.value = msg.banner_design_inherited ?? ''
    bannerFrom.value = msg.banner_from ?? null
    if (banner.value.trim()) previewBanner()
  } else if (msg.type === 'saved') {
    saveError.value = ''
    pendingSave = null
    refreshOnSave?.()
    refreshOnSave = null
  } else if (msg.type === 'error') {
    saveError.value = '⚠️ changes could not be saved'
  }
}

function onKeydown(ev) {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === 's') {
    ev.preventDefault()
    save()
  }
}

function connect() {
  ws = new WebSocket(
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/_api/ws/editor`,
  )
  ws.onmessage = onMessage
  ws.onopen = () => {
    reconnectDelay = 2000
    if (everConnected) {
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
  addEventListener('pagerite:editor-shown', onEditorShown)
  await loadSettings()
})

onUnmounted(() => {
  clearTimeout(reconnectTimer)
  for (const t of Object.values(timers)) clearTimeout(t)
  if (ws) {
    ws.onclose = null // intentional close, no reconnect
    ws.close()
  }
  view?.destroy()
  removeEventListener('keydown', onKeydown)
  removeEventListener('pagerite:editor-shown', onEditorShown)
})
</script>

<template>
  <div class="banner-editor">
    <div v-if="saveError">{{ saveError }}</div>

    <section class="block" @paste="onBannerPaste">
      <div class="block-head">
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
          class="icon-btn"
          title="upload banner image/video (replaces existing media) — pasting works too"
          @click="fileInput.click()"
        >🖼️</button>
        <input
          ref="fileInput"
          type="file"
          accept="image/*,video/*"
          hidden
          @change="(ev) => { uploadBannerMedia(ev.target.files[0]); ev.target.value = '' }"
        />
      </div>
      <div v-if="bannerFrom !== null" class="note">
        left empty, the banner code is inherited from /{{ bannerFrom }}
      </div>
      <div ref="bannerEl" class="banner-cm" />
    </section>
  </div>
</template>

<style scoped>
.banner-editor {
  display: flex;
  flex-direction: column;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  background: var(--surface);
  flex: 1;
  min-height: 0;
}

.block-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.note {
  color: var(--muted);
  font-size: 0.8rem;
}

.block-head .icon-btn {
  margin-left: auto;
  padding: 0 0.2rem;
  font-size: 1rem;
  background: none;
  border: none;
  cursor: pointer;
  opacity: 0.7;
}

.block-head .icon-btn:hover {
  opacity: 1;
}

/* The banner design selector stays compact; the upload button is pushed
   right by its auto margin. */
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

/* CodeMirror window for the banner HTML; fills the tab and scrolls
   internally. */
.banner-cm {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--line);
  border-radius: 4px;
  overflow: hidden;
}

.banner-cm :deep(.cm-editor) {
  height: 100%;
  font-size: 0.85rem;
}

.banner-cm :deep(.cm-scroller) {
  overflow: auto;
}

.banner-cm :deep(.cm-gutters) {
  display: none;
}
</style>
