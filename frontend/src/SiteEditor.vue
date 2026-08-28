<script setup>
// Site settings tab: site-wide brand, custom brand HTML, theme, favicon,
// custom CSS and fonts. The site structure tree lives in StructureEditor.
// Everything saves immediately as you edit — no save button, no edit mode.
import { computed, onActivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { css } from '@codemirror/lang-css'
import { html } from '@codemirror/lang-html'
import { cmHighlight, cmTheme } from './cmtheme'
import { dropPageCache, loadPlain, runScripts } from './swapdoc'

const props = defineProps({
  pagePath: { type: String, default: '' },
})
// close/path-change are wired by EditorShell; this tab never emits them.
defineEmits(['close', 'pathChange'])

const path = ref('')
const saveError = ref('')
const brandInput = ref(null)
const brandEl = ref(null)
const cssEl = ref(null)

let cssView = null      // CodeMirror for the site-wide custom CSS
let cssSyncing = false  // set while replacing the CSS document programmatically
const customCss = ref('')

let brandView = null      // CodeMirror for the custom brand HTML
let brandSyncing = false  // set while replacing the document programmatically
const brandHtml = ref('')

function normPath(p) {
  return p.trim().replace(/^\/+|\/+$/g, '')
}

// Debounce per key: text edits save while typing, without a request per
// keystroke.
const timers = {}
function debounce(key, fn, ms = 600) {
  clearTimeout(timers[key])
  timers[key] = setTimeout(fn, ms)
}

// Human-readable reason from a failed API call (FastAPI errors carry a
// JSON {detail}), falling back to a generic message.
async function errorDetail(res) {
  const body = await res.json().catch(() => null)
  return body?.detail || 'changes could not be saved'
}

// --- Site-wide brand (header link + <title> suffix) ----------------------
// Edits apply to the live page immediately and save while typing. An
// empty brand removes the header link and the title suffix entirely.
const brand = ref('')
const theme = ref('')
// Theme options come from the backend (theme folders on disk, see GET
// /_api/settings), so added themes need no frontend changes.
const themeOptions = ref([{ value: '', label: 'none' }])
// Page transition (cube, crossfade, ...): a design folder with
// transition.css under pagerite/themes/, injected as #pagerite-transition.
const transition = ref('cube')
const transitionOptions = ref([])

async function loadSettings() {
  try {
    const s = await (await fetch('/_api/settings')).json()
    brand.value = s.brand
    brandHtml.value = s.brand_html || ''
    setBrandDocument(brandHtml.value)
    previewBrand()
    theme.value = s.theme || ''
    customCss.value = s.custom_css || ''
    favicon.value = s.favicon || ''
    themeOptions.value = [
      { value: '', label: 'none' },
      ...(s.themes || []).map((t) => ({ value: t, label: t })),
    ]
    transition.value = s.transition || 'cube'
    transitionOptions.value = s.transitions || []
  } catch { /* keep default */ }
}

// --- Favicon ---------------------------------------------------------------
// Clicking the preview tile uploads a new one into the content-addressed
// file store (PUT /_api/settings/favicon), linked on every page as
// <link rel="icon">; empty falls back to the build's /favicon.ico. Applies
// to the live page immediately.
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
    dropPageCache()
  } else {
    saveError.value = `⚠️ ${await errorDetail(res)}`
  }
}

// The site settings' window title shows the site brand (or a generic label)
// plus the edit pen; the current article title is irrelevant here.
function updateEditorTitle() {
  document.title = `${brand.value.trim() || 'Pagerite'} 🖊️`
}

function applyBrand(b) {
  let el = document.getElementById('brand')
  if (b) {
    if (el && el.tagName !== 'A') {
      // Currently the custom-HTML wrapper: swap back to a plain link.
      el.remove()
      el = null
    }
    if (!el) {
      el = document.createElement('a')
      el.id = 'brand'
      el.href = '/'
      document.getElementById('nav')?.before(el)
    }
    el.textContent = b
  } else {
    if (el) el.remove()
  }
  updateEditorTitle()
}

// Custom brand HTML (site-wide, replaces the brand link entirely) is
// previewed into the header on every keystroke and saves debounced.
function previewBrand() {
  if (brandHtml.value.trim()) {
    const el = document.createElement('div')
    el.id = 'brand'
    el.innerHTML = brandHtml.value
    const old = document.getElementById('brand')
    if (old) old.replaceWith(el)
    else document.getElementById('nav')?.before(el)
    runScripts(el)
  } else {
    applyBrand(brand.value)
  }
}

function setBrandDocument(text) {
  brandSyncing = true
  brandView.dispatch({ changes: { from: 0, to: brandView.state.doc.length, insert: text } })
  brandSyncing = false
  brandHtml.value = text
}

function onBrandHtmlInput() {
  previewBrand()
  debounce('brand-html', () => saveSettings(), 400)
}

// Brand media goes to the shared content store, like banner media.
async function uploadBrandMedia(file) {
  if (!file || !/^(image|video)\//.test(file.type)) return
  const name = file.name.replace(/[^\w.-]/g, '-')
  const res = await fetch(`/_api/files/${encodeURIComponent(name)}`, { method: 'PUT', body: file })
  if (!res.ok) return
  const { path: stored } = await res.json()
  const tag = file.type.startsWith('video/')
    ? `<video src="${stored}" autoplay muted loop playsinline></video>`
    : `<img src="${stored}" alt="">`
  const rest = stripBrandMedia(brandHtml.value)
  setBrandDocument(rest ? `${tag}\n${rest}` : tag)
  previewBrand()
  saveSettings()
}

function stripBrandMedia(html) {
  // The brand has one piece of media: uploading replaces earlier img/video
  // tags instead of stacking them. (Other HTML, e.g. canvas+script, stays.)
  const doc = new DOMParser().parseFromString(html, 'text/html')
  for (const el of doc.querySelectorAll('img, video')) el.remove()
  return doc.body.innerHTML.trim()
}

function onBrandPaste(ev) {
  const file = [...(ev.clipboardData?.files || [])]
    .find((f) => /^(image|video)\//.test(f.type))
  if (file) {
    ev.preventDefault()
    uploadBrandMedia(file)
  }
}

function onBrandInput() {
  // Custom brand HTML wins over the plain text brand in the header.
  if (!brandHtml.value.trim()) applyBrand(brand.value)
  else updateEditorTitle()
  debounce('brand', saveSettings)
}

watch(brand, updateEditorTitle, { immediate: true })
watch(() => props.pagePath, (p) => { path.value = normPath(p) })
onActivated(updateEditorTitle)

// The shell stays mounted while hidden: when it is re-shown with this tab
// active, restore the window title.
function onEditorShown() {
  if (document.body.dataset.editorMode === 'site') updateEditorTitle()
}

async function saveSettings(opts = {}) {
  const res = await fetch('/_api/settings', {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      brand: brand.value,
      theme: theme.value,
      custom_css: customCss.value,
      brand_html: brandHtml.value,
      transition: transition.value,
      ...opts,
    }),
  })
  if (res.ok) {
    saveError.value = ''
    dropPageCache()
  } else {
    saveError.value = '⚠️ changes could not be saved'
  }
}

async function onThemeChange() {
  await saveSettings()
  // Theme CSS is backend-served at /_themes/{theme}/theme.css in both dev
  // and prod, but rendered differently: a <link> in dev, an inline <style>
  // in prod. Swap it in place, then re-render (the theme's default banner
  // design and the page's stylesheets may change with it).
  let el = document.getElementById('pagerite-theme')
  const url = `/_themes/${theme.value}/theme.css`
  if (theme.value) {
    if (el?.tagName === 'STYLE') {
      el.textContent = await (await fetch(url)).text()
    } else if (el) {
      el.href = url
    } else if (import.meta.env.DEV) {
      // Re-create after "none": keep base < theme < design < custom CSS.
      // In dev there is no #pagerite-base element (the base is a
      // Vite-injected <style>), so anchor to the next sheet instead of
      // prepending before the base styles.
      el = document.createElement('link')
      el.rel = 'stylesheet'
      el.id = 'pagerite-theme'
      el.href = url
      const before = document.getElementById('pagerite-base')?.nextSibling
        ?? document.getElementById('pagerite-banner')
        ?? document.getElementById('pagerite-user')
      if (before) before.before(el)
      else document.head.append(el)
    } else {
      // Prod: inline <style>, fetched from the backend-served URL.
      el = document.createElement('style')
      el.id = 'pagerite-theme'
      el.textContent = await (await fetch(url)).text()
      const before = document.getElementById('pagerite-base')?.nextSibling
        ?? document.getElementById('pagerite-banner')
        ?? document.getElementById('pagerite-user')
      if (before) before.before(el)
      else document.head.append(el)
    }
  } else if (el) {
    el.remove()
  }
  loadPlain(path.value)
}

async function onTransitionChange() {
  await saveSettings()
  // Transition CSS is backend-served at /_themes/{name}/transition.css in
  // both dev and prod (<link> in dev, inline <style> in prod), like the
  // theme. Swap #pagerite-transition in place — it only styles view
  // transitions, so no re-render of the page regions is needed.
  let el = document.getElementById('pagerite-transition')
  const url = `/_themes/${transition.value}/transition.css`
  if (el?.tagName === 'STYLE') {
    el.textContent = await (await fetch(url)).text()
  } else if (el) {
    el.href = url
  } else {
    // Missing (created before this feature, or "none" saved directly):
    // re-create, keeping base < theme < design < transition < custom CSS.
    if (import.meta.env.DEV) {
      el = document.createElement('link')
      el.rel = 'stylesheet'
      el.href = url
    } else {
      el = document.createElement('style')
      el.textContent = await (await fetch(url)).text()
    }
    el.id = 'pagerite-transition'
    const before = document.getElementById('pagerite-banner')?.nextSibling
      ?? document.getElementById('pagerite-user')
    if (before) before.before(el)
    else document.head.append(el)
  }
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
  debounce('custom-css', saveSettings, 400)
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

onMounted(async () => {
  path.value = normPath(props.pagePath)
  cssView = new EditorView({
    state: EditorState.create({
      doc: '',
      extensions: [
        basicSetup,
        css(),
        cmTheme,
        cmHighlight,
        EditorView.lineWrapping,
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
  brandView = new EditorView({
    state: EditorState.create({
      doc: '',
      extensions: [
        basicSetup,
        html(),
        cmTheme,
        cmHighlight,
        EditorView.lineWrapping,
        EditorView.updateListener.of((u) => {
          if (u.docChanged && !brandSyncing) {
            brandHtml.value = brandView.state.doc.toString()
            onBrandHtmlInput()
          }
        }),
      ],
    }),
    parent: brandEl.value,
  })
  await loadSettings()
  parseFonts(customCss.value)
  setCssDocument(customCss.value)
  applyCustomCss(customCss.value)
  addEventListener('pagerite:editor-shown', onEditorShown)
})

onUnmounted(() => {
  for (const t of Object.values(timers)) clearTimeout(t)
  cssView?.destroy()
  brandView?.destroy()
  removeEventListener('pagerite:editor-shown', onEditorShown)
})
</script>

<template>
  <div class="site-editor">
    <div v-if="saveError">{{ saveError }}</div>

    <section class="block">
      <div class="field-grid">
        <label class="field">
          <span class="field-label">site name</span>
          <input
            v-model="brand"
            class="text-input"
            title="Site name (header link and window title)"
            @input="onBrandInput"
          />
        </label>
        <div class="field">
          <span class="field-label">favicon</span>
          <button
            type="button"
            class="favicon-tile"
            title="upload favicon (ico, png, svg...)"
            @click="faviconInput.click()"
          >
            <img v-if="favicon" :src="favicon" alt="current favicon" />
            <span v-else>?</span>
          </button>
          <input
            ref="faviconInput"
            type="file"
            accept="image/*"
            hidden
            @change="(ev) => { uploadFavicon(ev.target.files[0]); ev.target.value = '' }"
          />
        </div>
        <div class="field">
          <span class="field-label">theme</span>
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
        </div>
        <label class="field">
          <span class="field-label">transition</span>
          <select
            v-model="transition"
            class="text-input theme-select"
            title="Page transition"
            @change="onTransitionChange"
          >
            <option v-for="t in transitionOptions" :key="t" :value="t">
              {{ t }}
            </option>
          </select>
        </label>
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
    </section>

    <section class="block grow" @paste="onBrandPaste">
      <div class="block-head">
        <span class="field-label">brand code (replaces the brand link)</span>
        <button
          type="button"
          class="icon-btn"
          title="upload brand image/video (replaces existing media) — pasting works too"
          @click="brandInput.click()"
        >🖼️</button>
        <input
          ref="brandInput"
          type="file"
          accept="image/*,video/*"
          hidden
          @change="(ev) => { uploadBrandMedia(ev.target.files[0]); ev.target.value = '' }"
        />
      </div>
      <div ref="brandEl" class="brand-cm" />
    </section>

    <section class="block grow">
      <div class="block-head">
        <span class="field-label">custom CSS (applies to every page, on top of the theme)</span>
      </div>
      <div ref="cssEl" class="css-cm" />
    </section>
  </div>
</template>

<style scoped>
.site-editor {
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  background: var(--surface);
}

.block {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

/* Editor blocks (brand HTML, custom CSS) share the leftover panel height
   equally; their CodeMirror windows fill the block and scroll internally. */
.block.grow {
  flex: 1;
  min-height: 7rem;
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

/* Two-column grid (site name + favicon, theme + transition) so the rows
   and labels align with each other. */
.field-grid {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 0.4rem 1.5rem;
}

/* Labels share one narrow track per field, sized to the longest label
   ("transition"), so they line up across rows without excess space. */
.field-grid > .field {
  display: grid;
  grid-template-columns: 4.3rem 1fr auto;
}

.field-grid .field-label {
  min-width: 0;
}

/* Fixed-width labels keep the settings rows aligned. */
.field > .field-label {
  min-width: 5rem;
}

.field-label {
  color: var(--muted);
  font-size: 0.85rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Favicon: the preview tile itself is the upload button — click to pick a
   new image. */
.favicon-tile {
  width: 1.8rem;
  height: 1.8rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  background: var(--bg);
  color: var(--muted);
  border: 1px solid var(--line);
  border-radius: 4px;
  cursor: pointer;
}

.favicon-tile:hover {
  border-color: var(--accent);
}

.favicon-tile img {
  width: 1.4rem;
  height: 1.4rem;
  object-fit: contain;
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

/* CodeMirror windows for the brand HTML and custom CSS: fill the growing
   block, scroll internally. */
.brand-cm,
.css-cm {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--line);
  border-radius: 4px;
  overflow: hidden;
}

.brand-cm :deep(.cm-editor),
.css-cm :deep(.cm-editor) {
  height: 100%;
  font-size: 0.85rem;
}

.brand-cm :deep(.cm-scroller),
.css-cm :deep(.cm-scroller) {
  overflow: auto;
}

.brand-cm :deep(.cm-gutters),
.css-cm :deep(.cm-gutters) {
  display: none;
}
</style>
