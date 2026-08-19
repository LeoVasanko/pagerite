<script setup>
// Tabbed shell for the four admin editors. The individual pens are shorthands
// that open the shell on a given tab; once open, tabs switch instantly without
// closing the panel. Tabs are kept alive so switching preserves state.
import { onMounted, onUnmounted, provide, ref, watch } from 'vue'
import PageEditor from './PageEditor.vue'
import BannerEditor from './BannerEditor.vue'
import SiteEditor from './SiteEditor.vue'
import StructureEditor from './StructureEditor.vue'

const props = defineProps({
  pagePath: { type: String, default: '' },
  initialMode: { type: String, default: 'page' },
})
const emit = defineEmits(['close'])

const currentPath = ref(props.pagePath)
const activeMode = ref(props.initialMode)

// Tab order: site-wide settings first (site, structure), then — after a
// visual break — the per-page editors (article, banner).
const MODES = [
  { key: 'site', label: 'site', component: SiteEditor },
  { key: 'structure', label: 'structure', component: StructureEditor },
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
// preview via its own pagerite:editor-shown listener.
function onKeydown(ev) {
  if (ev.key === 'Escape' && document.body.classList.contains('editing')) close()
}

onMounted(() => {
  document.body.dataset.editorMode = activeMode.value
  addEventListener('pagerite:switch-editor', onSwitchEvent)
  addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  removeEventListener('pagerite:switch-editor', onSwitchEvent)
  removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="editor-root overlay">
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
