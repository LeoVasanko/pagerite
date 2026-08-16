<script setup>
// Recursive site-structure tree with drag-and-drop ordering (vue-draggable).
// Nodes come from the server (GET /_api/pages via SiteEditor.vue) as
// {slug, path, title, order, published, has_content, children}.
// Every node is real: a label whose title and slug are always editable
// inline — the title saves while typing (and focusing it opens the page),
// the slug commits on blur/Enter since it renames the path, moving the
// whole subtree. Nodes without content are category labels whose URL
// renders a placeholder page; the ➕ on their row gives them a landing page.
// Every non-empty list (and the root list) ends with a ➕ footer row:
// clicking it adds a *pending* row (a local-only item persisted to the
// server only on commit, ✓/Enter, Esc discards) at the end of that list,
// and while dragging it doubles as the list's "end of list" drop target.
// Leaf pages have no ➕ row of their own, but every row's child list is a
// drop target: its (invisible) container box reaches up over the bottom of
// its row, so dropping ON a row makes it a child (first position), while
// dropping on the row's top edge inserts a sibling before it. See the
// `.node > .treelist` style.
// The front page is the root row with an empty slug: renaming it away
// leaves no front page, and giving another top-level row the empty slug
// makes it the front page. Delete is a two-step inline button (no dialog).
// Actions are injected from SiteEditor.vue to avoid per-level event
// forwarding.
import { inject } from 'vue'
import draggable from 'vuedraggable'
import { slugify } from './slugify'

defineOptions({ name: 'StructureTree' })
const props = defineProps({
  nodes: { type: Array, required: true },
  parentPath: { type: String, default: '' },
  depth: { type: Number, default: 0 },
})

const handlers = inject('structureHandlers')

// Live-filter the slug inputs as they are typed (oninput): invalid
// characters are simply not accepted, spaces become hyphens and unicode
// folds to ASCII (see slugify.js). Existing rows commit on change, the
// pending row is v-modeled.
function onSlugInput(ev) {
  ev.target.value = slugify(ev.target.value)
}

function onPendingSlugInput(element, ev) {
  element.slug = slugify(ev.target.value)
}

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

// While dragging, the child-list overlap strips become hit-testable (see
// styles). Purely functional — nothing is shown or resized.
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
        <!-- Indentation is structural: each nested treelist margin-indents
             itself relative to its parent (see styles), so a dragged row
             previews its whole subtree at the target list's depth. -->
        <div
          class="row"
          :class="{ current: element.path === handlers.current() }"
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
              placeholder="slug — empty: derived from the title"
              @input="onPendingSlugInput(element, $event)"
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
              @input="onSlugInput"
              @change="handlers.commitSlug(element, $event)"
            />
            <span class="acts">
              <span v-if="!element.published" class="draft">draft</span>
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
    <!-- Non-draggable footer (vuedraggable slot): a ➕ row at the end of
         the list. Clicking adds a pending page at this level; while
         dragging it is the list's "end of list" drop target. Only shown
         where the list has items (or at the root, to add the first page). -->
    <template #footer>
      <div v-if="nodes.length || !depth" class="add-row">
        <button
          type="button"
          class="add"
          title="new page here — fill in title and slug, then drag into place if needed"
          @click="handlers.newPage(nodes)"
        >➕</button>
      </div>
    </template>
  </draggable>
</template>

<style scoped>
.treelist {
  min-height: 0;
  /* Flex column so the ➕ footer can render last via `order` even when
     Sortable's end-of-list insertion appends the preview after it in the
     DOM (order only affects painting; Sortable's item rect math and
     vuedraggable's index mapping still see the logical DOM order). */
  display: flex;
  flex-direction: column;
  /* Child lists overlap the bottom of their own row (below); the overlap
     strip must not block clicks on the row's inputs, so containers are
     hit-test transparent and only the rows re-enable pointer events. */
  pointer-events: none;
}

/* While dragging, the overlap strips become hit-testable so a row's child
   list can be targeted. No visual change. */
body.tree-dragging .treelist {
  pointer-events: auto;
}

/* A node's child list indents itself (structural indentation: a dragged
   row's subtree follows its head to the target depth automatically) and
   reaches up over the bottom ~60% of its row: the negative top margin
   pulls the container up, the equal padding pushes its children back
   down — zero layout or visual effect, but while dragging Sortable sees
   the lower part of the row as inside the child list and natively inserts
   there as the first child (a leaf's empty child list becomes a sublist
   this way). The exposed top strip of the row remains the parent list's
   target = sibling before this row. */
.node > .treelist {
  margin-top: -0.95rem;
  padding-top: 0.95rem;
  margin-left: 1.1rem;
}

.node {
  margin-left: 0.2rem;
}

/* Grid rows: handle / title / slug / actions line up as columns within a
   level (nesting indents the whole list container, so columns align per
   level, not across levels). */
.row {
  display: grid;
  grid-template-columns: 1.2em minmax(3rem, 1fr) 7rem 5rem;
  align-items: baseline;
  gap: 0.35rem;
  /* Vertical spacing widens the drop zones: the exposed top strip is the
     "sibling before" target, the overlapped bottom is "child of". */
  padding-top: 0.15rem;
  padding-bottom: 0.15rem;
  /* Rows (and the ➕ row) stay clickable despite .treelist's pointer-events: none. */
  pointer-events: auto;
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

/* The ➕ footer row: subtle always-visible "add here" button at the end of
   a list; while dragging it is the list's "end of list" drop target. */
.add-row {
  min-height: 1.1rem;
  display: flex;
  align-items: center;
  pointer-events: auto; /* see .row */
  order: 1; /* always render after the rows, even mid-drag (see .treelist) */
}

.add {
  margin-left: 1.2em; /* align with the row titles, past the drag handle */
  padding: 0 0.3rem;
  background: none;
  border: none;
  font-size: 0.9rem;
  cursor: pointer;
  opacity: 0.5;
}

.add:hover {
  opacity: 1;
}
</style>
