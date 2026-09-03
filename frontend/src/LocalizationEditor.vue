<script setup>
// Lang tab: the site-wide translation target languages (translate_langs)
// and the translator service WebSocket URL(s) (translate_keys). ALL
// languages are listed, English included — a page whose primary language
// (Node.language, configured per row in the structure tab, inherited down
// the hierarchy) differs can be translated INTO any other. Flag clicks
// toggle and save immediately; the settings round-trip re-reads the
// payload, so this tab only ever changes translate_langs. The settings
// write's invalidation hook kicks the translation dispatcher. The refresh
// button drops all machine translations (user patches are kept), making
// the dispatcher re-translate everything.
import { computed, onActivated, onMounted, onUnmounted, ref } from 'vue'
import { LANG_GROUPS, TRANSLATABLE, flagFor, langName } from './langs'
import { dropPageCache } from './swapdoc'

defineProps({ pagePath: { type: String, default: '' } })
// close/path-change are wired by EditorShell; this tab never emits them.
defineEmits(['close', 'pathChange'])

const saveError = ref('')
const selected = ref(new Set())
const keyUrls = ref([])

// The toggleable targets: every translatable language, laid out in
// geographic/cultural groups (one row each) rather than alphabetized —
// related languages sit together (a node's own primary is excluded per
// article, server-side). Any code missing from LANG_GROUPS trails as an
// extra row.
const groups = computed(() => {
  const tile = (code) => ({ code, name: langName(code), flag: flagFor(code) })
  const rows = LANG_GROUPS.map((g) => g.filter((c) => c in TRANSLATABLE).map(tile))
  const covered = new Set(LANG_GROUPS.flat())
  const rest = Object.keys(TRANSLATABLE).filter((c) => !covered.has(c)).map(tile)
  if (rest.length) rows.push(rest)
  return rows.filter((r) => r.length)
})

function updateWindowTitle() {
  document.title = 'lang 🖊️'
}

onActivated(updateWindowTitle)

// The shell stays mounted while hidden: when it is re-shown with this tab
// active, restore the window title.
function onEditorShown() {
  if (document.body.dataset.editorMode === 'localization') updateWindowTitle()
}

onMounted(async () => {
  addEventListener('pagerite:editor-shown', onEditorShown)
  try {
    const s = await (await fetch('/_api/settings')).json()
    selected.value = new Set(s.translate_langs || [])
    const wsBase = location.origin.replace(/^http/, 'ws')
    keyUrls.value = Object.entries(s.translate_keys || {})
      .map(([key, name]) => ({ name, url: `${wsBase}/_translate/${key}` }))
  } catch { /* keep defaults */ }
})

onUnmounted(() => removeEventListener('pagerite:editor-shown', onEditorShown))

async function toggle(code) {
  const next = new Set(selected.value)
  if (next.has(code)) next.delete(code)
  else next.add(code)
  selected.value = next
  try {
    const s = await (await fetch('/_api/settings')).json()
    const res = await fetch('/_api/settings', {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...s, translate_langs: [...next] }),
    })
    if (res.ok) {
      saveError.value = ''
      dropPageCache()
    } else {
      saveError.value = '⚠️ changes could not be saved'
    }
  } catch {
    saveError.value = '⚠️ changes could not be saved'
  }
}

// Delete all machine translations server-side; the dispatcher re-fills
// them (a connected translator starts getting jobs right away). User
// patches survive — they are edits, not machine output.
const refreshing = ref(false)
async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const res = await fetch('/_api/translations', { method: 'DELETE' })
    saveError.value = res.ok ? '' : '⚠️ translations could not be refreshed'
    if (res.ok) dropPageCache()
  } catch {
    saveError.value = '⚠️ translations could not be refreshed'
  } finally {
    refreshing.value = false
  }
}
</script>

<template>
  <div class="localization-editor">
    <div v-if="saveError">{{ saveError }}</div>

    <section class="block">
      <div class="block-head">
        <span class="field-label">languages</span>
      </div>
      <div class="flags">
        <div v-for="(row, ri) in groups" :key="ri" class="flag-row">
          <button
            v-for="o in row"
            :key="o.code"
            type="button"
            class="flag-tile"
            :class="{ selected: selected.has(o.code) }"
            :title="`${o.name} (${o.code})`"
            @click="toggle(o.code)"
          >
            <span class="flag" v-html="o.flag" />
          </button>
        </div>
      </div>
    </section>

    <section class="block">
      <div class="block-head">
        <span class="field-label">translations</span>
        <small class="muted">deleting re-translates everything; user edits are kept</small>
      </div>
      <button
        type="button"
        class="refresh-btn"
        :disabled="refreshing"
        title="delete all machine translations and let the translator re-fill them"
        @click="refresh"
      >
        {{ refreshing ? 'refreshing…' : 'refresh all translations' }}
      </button>
    </section>

    <section v-if="keyUrls.length" class="block">
      <div class="block-head">
        <span class="field-label">translator service</span>
        <small class="muted">connect scripts/translator.py to</small>
      </div>
      <div v-for="k in keyUrls" :key="k.url" class="key-row">
        <code>{{ k.url }}</code>
        <small class="muted">{{ k.name }}</small>
      </div>
    </section>
  </div>
</template>

<style scoped>
.localization-editor {
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

.block-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.field-label {
  color: var(--muted);
  font-size: 0.85rem;
}

.muted {
  color: var(--muted);
}

/* Flag grid: one geographic group per row. Deselected flags sit dimmed and
   grayed; a click brings one to full color (selected = a translation
   target) — the shading alone carries the state, no outline. */
.flags {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.2rem 0;
}

.flag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.flag-tile {
  padding: 3px;
  background: none;
  border: 2px solid transparent;
  border-radius: 5px;
  cursor: pointer;
  opacity: 0.4;
  filter: grayscale(0.8);
  transition: opacity 0.15s, filter 0.15s, border-color 0.15s;
}

.flag-tile:hover {
  opacity: 0.8;
  filter: none;
}

.flag-tile.selected {
  opacity: 1;
  filter: none;
}

/* Same flag chips as the PageEditor language picker / analytics cells. */
.flag {
  display: inline-flex;
  width: 18px;
  height: 12px;
  flex: 0 0 auto;
  border-radius: 2px;
  overflow: hidden;
  border: 1px solid var(--line);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.2) inset;
}

.flag-tile .flag {
  width: 36px;
  height: 24px;
}

.flag :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}

.key-row {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
}

.key-row code {
  user-select: all;
}

.refresh-btn {
  align-self: flex-start;
  margin-bottom: 0.2rem;
  padding: 0.3rem 0.8rem;
  font: inherit;
  font-size: 0.85rem;
  color: var(--muted);
  background: none;
  border: 1px solid var(--line);
  border-radius: 5px;
  cursor: pointer;
}

.refresh-btn:hover:not(:disabled) {
  color: var(--text);
  border-color: var(--muted);
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
