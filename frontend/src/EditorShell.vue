<script setup>
// Tabbed shell for the five admin editors. The individual pens are shorthands
// that open the shell on a given tab; once open, tabs switch instantly without
// closing the panel. Tabs are kept alive so switching preserves state.
import { onMounted, onUnmounted, provide, ref, watch } from 'vue'
import PageEditor from './PageEditor.vue'
import BannerEditor from './BannerEditor.vue'
import SiteEditor from './SiteEditor.vue'
import StructureEditor from './StructureEditor.vue'
import LocalizationEditor from './LocalizationEditor.vue'
import { editorLang, pagePrimary } from './editorLang'
import { loadPlain, setLangOverride } from './swapdoc'

const props = defineProps({
  pagePath: { type: String, default: '' },
  initialMode: { type: String, default: 'page' },
})
const emit = defineEmits(['close'])

const currentPath = ref(props.pagePath)
const activeMode = ref(props.initialMode)

// The shared language selection (./editorLang, v-modeled by the tabs'
// LangSelects) also drives the page preview: while the shell is open it
// overrides the normal language preferences (?lang= / Accept-Language),
// so the page renders in the language being edited; closing restores.
// The primary selection pins by the CURRENT PAGE's own primary language
// (pages may differ — Node.language is inherited down the tree).
let pinned = false
function pinPreviewLang() {
  pinned = true
  // '' pagePrimary = not yet learned: pin 'en', the server's final fallback
  // (i18n.ORIGINAL_LANGUAGE).
  setLangOverride(editorLang.value || pagePrimary.value || 'en')
  loadPlain(currentPath.value)
}
function unpinPreviewLang() {
  if (!pinned) return
  pinned = false
  setLangOverride(null)
  loadPlain(currentPath.value)
}
watch(editorLang, () => { if (pinned) pinPreviewLang() })
// The page's primary may be (re)learned while pinned on it (doc accept,
// tree refresh, a language change on the row) — re-pin with the new code.
watch(pagePrimary, () => { if (pinned && !editorLang.value) pinPreviewLang() })

// Tab order: site-wide settings first (site, structure, localization), then
// — after a visual break — the per-page editors (article, banner).
const MODES = [
  { key: 'site', label: 'site', component: SiteEditor },
  { key: 'structure', label: 'structure', component: StructureEditor },
  { key: 'localization', label: 'lang', component: LocalizationEditor },
  { key: 'page', label: 'article', component: PageEditor, breakBefore: true },
  { key: 'banner', label: 'banner', component: BannerEditor },
]

function switchMode(mode) {
  if (MODES.some((m) => m.key === mode)) activeMode.value = mode
}

function onPathChange(path) {
  currentPath.value = path
}

function close() {
  emit('close')
}

provide('editorShell', { switchMode })

watch(activeMode, (mode) => {
  document.body.dataset.editorMode = mode
}, { immediate: true })

// A pen click while the shell is open (main.js) switches tabs, and also
// retargets the editors when the user fetch-navigated with the shell open.
function onSwitchEvent(ev) {
  if (ev.detail?.path != null) currentPath.value = ev.detail.path
  if (ev.detail?.mode) switchMode(ev.detail.mode)
}

// Closing the shell hides it but keeps it mounted (main.js); the tabs stay
// cached in KeepAlive the whole time, so no state is ever lost until a real
// page reload. On re-show each active tab re-applies its window title and
// preview via its own pagerite:editor-shown listener. No Escape-to-close:
// it fired too easily by accident (e.g. dismissing an editor popup).

onMounted(() => {
  document.body.dataset.editorMode = activeMode.value
  addEventListener('pagerite:switch-editor', onSwitchEvent)
  addEventListener('pagerite:editor-shown', pinPreviewLang)
  addEventListener('pagerite:editor-hidden', unpinPreviewLang)
  // The shell mounts visible (openEditor), so pin immediately. The site
  // default primary language comes from the settings — it only fills the
  // unknown; the page/structure tabs refine pagePrimary per page as they
  // learn it (their knowledge is strictly better).
  pinPreviewLang()
  fetch('/_api/settings').then((r) => r.json()).then((s) => {
    if (!pagePrimary.value) pagePrimary.value = s.primary_lang || 'en'
  }).catch(() => { /* keep the fallback */ })
})

onUnmounted(() => {
  removeEventListener('pagerite:switch-editor', onSwitchEvent)
  removeEventListener('pagerite:editor-shown', pinPreviewLang)
  removeEventListener('pagerite:editor-hidden', unpinPreviewLang)
})
</script>

<template>
  <div class="editor-root overlay" lang="en" dir="ltr">
    <header class="editor-tabs">
      <template v-for="m in MODES" :key="m.key">
        <span v-if="m.breakBefore" class="tab-break" />
        <button
          type="button"
          class="tab"
          :class="{ active: activeMode === m.key }"
          @click="switchMode(m.key)"
        >
          {{ m.label }}
        </button>
      </template>
      <button type="button" class="close" title="close" @click="close">✕</button>
    </header>
    <div class="editor-tab-body">
      <KeepAlive>
        <component
          :is="MODES.find((m) => m.key === activeMode).component"
          :key="activeMode"
          :page-path="currentPath"
          @close="close"
          @path-change="onPathChange"
        />
      </KeepAlive>
    </div>
  </div>
</template>

<style scoped>
/* Docked-overlay positioning (sticky, height, slide-in) comes from the
   global pagerite.css (.editor-root.overlay); the shell is a flex column so
   the tab body fills what the tab bar leaves. */
.editor-root {
  display: flex;
  flex-direction: column;
}

.editor-tabs {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.4rem 1rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
  flex-shrink: 0;
}

.editor-tabs .tab {
  padding: 0.25rem 0.8rem;
  font: inherit;
  font-size: 0.9rem;
  color: var(--muted);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
}

.editor-tabs .tab:hover {
  color: var(--text);
}

/* Visual break between the site-wide tabs and the per-page tabs. */
.editor-tabs .tab-break {
  align-self: stretch;
  margin: 0.2rem 0.5rem;
  border-left: 1px solid var(--line);
}

.editor-tabs .tab.active {
  color: var(--text);
  border-bottom-color: var(--accent);
}

.editor-tabs .close {
  margin-left: auto;
  padding: 0 0.3rem;
  background: none;
  border: none;
  color: var(--muted);
  font-size: 1.05rem;
  cursor: pointer;
}

.editor-tabs .close:hover {
  color: var(--text);
}

.editor-tab-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* The active tab component's root fills the body. */
.editor-tab-body > * {
  flex: 1;
  min-height: 0;
}
</style>
