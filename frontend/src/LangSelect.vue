<script setup>
// The editor shell's one language selector (page + structure tabs): a small
// flag button opening a clean dropdown, v-modeled on the shared editorLang
// ('' = the primary language). The lang tab's flag grid is a different
// control (toggles, not a select) and stays as it is.
import { computed, ref } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, required: true }, // [{tag, code, name, flag, primary}]
  title: { type: String, default: '' }, // toggle-button tooltip override
})
const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const toggleBtn = ref(null)
const popStyle = ref({})
const current = computed(
  () => props.options.find((o) => o.tag === props.modelValue) ?? props.options[0],
)

function toggle() {
  open.value = !open.value
  if (open.value) {
    // Position: fixed so the popup overflows the scrolling editor panel
    // onto the page area instead of being clipped by it.
    const r = toggleBtn.value.getBoundingClientRect()
    popStyle.value = { top: `${r.bottom + 2}px`, left: `${r.left}px` }
  }
}

function select(tag) {
  emit('update:modelValue', tag)
  open.value = false
}
</script>

<template>
  <span v-if="options.length > 1" class="lang-select">
    <button
      ref="toggleBtn"
      type="button"
      class="lang-current"
      :class="{ open }"
      :title="title || (current
        ? `language: ${current.name}${current.primary ? ' (primary)' : ''}`
        : '')"
      @click="toggle"
    ><span v-if="current?.flag" class="flag" v-html="current.flag" /></button>
    <span v-if="open" class="lang-pop" :style="popStyle" @mouseleave="open = false">
      <button
        v-for="o in options"
        :key="o.code"
        type="button"
        :class="{ active: o.tag === modelValue }"
        :title="o.primary ? `${o.name} — the primary language` : `${o.name} — translation`"
        @click="select(o.tag)"
      ><span v-if="o.flag" class="flag" v-html="o.flag" /> {{ o.name }}<small v-if="o.primary"> (primary)</small></button>
    </span>
  </span>
</template>

<style scoped>
.lang-select {
  position: relative;
  display: flex;
}

/* The closed state is just the small flag — no button chrome until hovered. */
.lang-current {
  display: flex;
  align-items: center;
  padding: 2px;
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.lang-current:hover,
.lang-current.open {
  border-color: var(--line);
}

/* The dropdown matches the page's existing popups (.picker-pop look).
   Fixed-positioned (anchored to the toggle's viewport rect on open) so it
   is not clipped by the editor panel's scrolling overflow. */
.lang-pop {
  position: fixed;
  z-index: 20;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.15rem;
  padding: 0.3rem;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: 6px;
  box-shadow: 0 4px 16px #0004;
  white-space: nowrap;
}

.lang-pop button {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.15rem 0.4rem;
  font: inherit;
  font-size: 0.9rem;
  text-align: left;
  color: var(--text);
  background: none;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.lang-pop button:hover {
  background: var(--surface);
}

.lang-pop button.active {
  color: var(--accent);
}

.lang-pop small {
  color: var(--muted);
}

/* Flags render like in the analytics visitor cells. */
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

.flag :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}
</style>
