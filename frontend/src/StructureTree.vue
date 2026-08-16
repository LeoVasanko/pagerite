<script setup>
// Recursive site-structure tree with drag-and-drop ordering (vue-draggable).
// Nodes come from the server (GET /_/api/pages via SiteEditor.vue) as
// {slug, path, title, order, published, has_content, children}.
// Every node is real: a label whose title and slug are always editable
// inline — the title saves while typing (and focusing it opens the page),
// the slug commits on blur/Enter since it renames the path, moving the
// whole subtree. Nodes without content are category labels that redirect
// to their first child; the ➕ on their row gives them a landing page.
// The ➕ in the panel header adds a *pending* row: a local-only item that
// can be dragged into place before its title/slug are filled in, and is
// persisted to the server only on commit (✓/Enter, Esc discards).
// The front page is the root row with an empty slug: renaming it away
// leaves no front page, and giving another top-level row the empty slug
// makes it the front page. Delete is a two-step inline button (no dialog).
// Actions are injected from SiteEditor.vue to avoid per-level event
// forwarding.
import { inject } from 'vue'
import draggable from 'vuedraggable'

defineOptions({ name: 'StructureTree' })
const props = defineProps({
  nodes: { type: Array, required: true },
  parentPath: { type: String, default: '' },
  depth: { type: Number, default: 0 },
})

const handlers = inject('structureHandlers')

// Focus the title input of a fresh pending row.
const vFocus = { mounted: (el) => el.focus() }

function onChange(evt) {
  handlers.reorder(props.parentPath, props.nodes, evt)
}

// Drag guard: the front page (slug "") is a top-level item — it cannot be
// dropped into a section (its empty slug is only valid at the root). And
// nothing may be dropped under itself or one of its own descendants.
function onMove(evt) {
  const el = evt.draggedContext.element
  if (el.pending) return true // unsaved row: position it anywhere
  const targetParent = evt.to.dataset.parent || ''
  if (el.slug === '') return targetParent === ''
  if (targetParent === el.path || targetParent.startsWith(`${el.path}/`)) return false
  return true
}

// While dragging, reveal empty child lists as drop zones (style.css) so a
// page can be moved under a childless page.
function onStart() {
  document.body.classList.add('tree-dragging')
}

function onEnd() {
  document.body.classList.remove('tree-dragging')
}
</script>

<template>
  <draggable
    class="treelist"
    :data-parent="parentPath"
    :list="nodes"
    item-key="path"
    group="sitetree"
    ghost-class="ghost"
    :move="onMove"
    @change="onChange"
    @start="onStart"
    @end="onEnd"
  >
    <template #item="{ element }">
      <div class="node">
        <!-- Indentation is row padding, not a container margin, so the
             slug and action columns stay aligned across nesting levels. -->
        <div
          class="row"
          :class="{ current: element.path === handlers.current() }"
          :style="depth ? { paddingLeft: `${depth * 1.1}rem` } : null"
        >
          <span class="drag" title="drag to reorder/move">⠿</span>
          <template v-if="element.pending">
            <input
              v-model="element.title"
              v-focus
              class="edit title-edit"
              placeholder="Title"
              @keyup.enter="handlers.commitPending()"
              @keyup.esc="handlers.discardPending()"
            />
            <input
              v-model="element.slug"
              class="edit slug-edit"
              placeholder="slug"
              @keyup.enter="handlers.commitPending()"
              @keyup.esc="handlers.discardPending()"
            />
            <span class="acts">
              <button type="button" class="act" title="create page" @click="handlers.commitPending()">✓</button>
              <button type="button" class="act del" title="discard" @click="handlers.discardPending()">✕</button>
            </span>
          </template>
          <template v-else>
            <input
              class="edit title-edit"
              :value="element.title"
              placeholder="Title"
              title="Label in the navigation — saves while typing; click opens the page"
              @input="handlers.titleInput(element, $event)"
              @focus="handlers.open(element.path)"
            />
            <input
              class="edit slug-edit"
              :value="element.slug"
              placeholder="front page"
              title="Slug (last path segment) — renames move the whole subtree. Empty at top level = front page"
              @change="handlers.commitSlug(element, $event)"
            />
            <span class="acts">
              <span v-if="!element.published" class="draft">draft</span>
              <button
                v-if="!element.has_content"
                type="button"
                class="act"
                title="add a landing page (currently redirects to the first child)"
                @click="handlers.addContent(element)"
              >➕</button>
              <button
                type="button"
                class="act del"
                :class="{ armed: handlers.arming() === element.path }"
                :title="element.children.length
                  ? 'delete the landing page (the category keeps its subpages)'
                  : 'delete page'"
                @click="handlers.armRemove(element)"
              >{{ handlers.arming() === element.path ? 'delete?' : '✕' }}</button>
            </span>
          </template>
        </div>
        <StructureTree
          v-if="element.slug !== '' && !element.pending"
          :nodes="element.children"
          :parent-path="element.path"
          :depth="depth + 1"
        />
      </div>
    </template>
  </draggable>
</template>

<style scoped>
.treelist {
  min-height: 0;
}

.node {
  margin-left: 0.2rem;
}

/* Grid rows: handle / title / slug / actions line up as columns. Rows are
   full width at every level (indentation is row padding) and the slug and
   action columns are fixed-width, so they align across nesting levels. */
.row {
  display: grid;
  grid-template-columns: 1.2em minmax(3rem, 1fr) 7rem 5rem;
  align-items: baseline;
  gap: 0.35rem;
}

.row.current .title-edit {
  color: var(--accent);
  font-weight: 500;
}

.drag {
  color: var(--muted);
  cursor: grab;
}

/* Rows are always editable: inputs stay borderless until interacted with. */
.edit {
  font: inherit;
  font-size: 0.85rem;
  padding: 0.1rem 0.4rem;
  background: transparent;
  color: var(--text);
  border: 1px solid transparent;
  border-radius: 4px;
  min-width: 0;
}

.edit:hover {
  border-color: var(--line);
}

.edit:focus {
  background: var(--bg);
  border-color: var(--accent);
  outline: none;
}

.title-edit {
  cursor: pointer;
}

.title-edit:focus {
  cursor: text;
}

.slug-edit {
  font-family: "Fira Code", monospace;
  color: var(--muted);
}

.acts {
  display: flex;
  align-items: baseline;
  gap: 0.25rem;
  justify-content: end;
}

.draft {
  color: var(--muted);
  font-size: 0.75rem;
}

.act {
  padding: 0 0.25rem;
  background: none;
  border: none;
  color: var(--muted);
  font-size: 0.8rem;
  cursor: pointer;
  white-space: nowrap;
}

/* Two-step delete: the first click arms the button, the second deletes. */
.act.armed {
  color: #e06c75;
  font-weight: 600;
}

.del:hover {
  color: #e06c75;
}

.ghost {
  opacity: 0.4;
}
</style>
