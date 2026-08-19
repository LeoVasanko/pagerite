<script setup>
// Structure tab: the draggable site structure tree. Focusing a page's row
// navigates to it in place (no transitions). Close and tab switching live
// in EditorShell.
//
// The tree comes from the server nested (GET /_api/pages); every node is
// real — a label with a title and slug, with content (landing page) or
// without (category whose URL renders a placeholder page). The front page
// is a top-level row with an empty slug, not the parent of the others.
import { inject, onActivated, onMounted, onUnmounted, provide, ref, watch } from 'vue'
import StructureTree from './StructureTree.vue'
import { slugify } from './slugify'
import { loadPlain } from './swapdoc'

const props = defineProps({
  pagePath: { type: String, default: '' },
})
const emit = defineEmits(['close', 'pathChange'])

const shell = inject('editorShell', null)

const path = ref('')
const saveError = ref('')
const tree = ref([])

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

function updateWindowTitle() {
  document.title = 'site structure 🖊️'
}

watch(() => props.pagePath, (p) => { path.value = normPath(p) })
onActivated(updateWindowTitle)

// The shell stays mounted while hidden: when it is re-shown with this tab
// active, restore the window title.
function onEditorShown() {
  if (document.body.dataset.editorMode === 'structure') updateWindowTitle()
}

// Tree row focus: switch the edited page and show it, skipping transitions.
async function navigate(p) {
  path.value = p
  emit('pathChange', p)
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
  // Hand over to the page editor tab for the actual writing.
  shell?.switchMode('page')
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

onMounted(() => {
  path.value = normPath(props.pagePath)
  refreshPages()
  addEventListener('pagerite:editor-shown', onEditorShown)
})

onUnmounted(() => {
  clearTimeout(armTimer)
  for (const t of Object.values(timers)) clearTimeout(t)
  removeEventListener('pagerite:editor-shown', onEditorShown)
})
</script>

<template>
  <div class="structure-editor">
    <div v-if="saveError">{{ saveError }}</div>
    <section class="block structure">
      <StructureTree :nodes="tree" />
    </section>
  </div>
</template>

<style scoped>
.structure-editor {
  display: flex;
  flex-direction: column;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.structure {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
</style>
