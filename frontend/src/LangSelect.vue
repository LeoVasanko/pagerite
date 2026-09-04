<script setup>
// The editor shell's one language selector (page + structure tabs): a small
// flag button opening a clean dropdown, v-modeled on the shared editorLang
// ('' = the primary language). The lang tab's flag grid is a different
// control (toggles, not a select) and stays as it is.
import { computed, nextTick, ref } from 'vue'
import { usePopup } from './dropdown'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, required: true }, // [{tag, code, name, flag, primary}]
  title: { type: String, default: '' }, // toggle-button tooltip override
})
const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const root = ref(null)
const toggleBtn = ref(null)
const pop = ref(null)
const popStyle = ref({})
// Closes on outside click / Escape (./dropdown), not on mouseleave.
usePopup(open, root)
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
    // A toggle mounted near the right window edge (the public page
    // selector sits top-right) opens the popup flush against that edge.
    nextTick(() => {
      const p = pop.value?.getBoundingClientRect()
      if (p && p.right > innerWidth - 4) {
        popStyle.value = {
          ...popStyle.value,
          left: `${Math.max(4, innerWidth - 4 - p.width)}px`,
        }
      }
    })
  }
}

function select(tag) {
  emit('update:modelValue', tag)
  open.value = false
}
</script>

<template>
  <span v-if="options.length > 1" ref="root" class="lang-select">
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
    <span v-if="open" ref="pop" class="lang-pop" :style="popStyle">
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

/* The closed state is just the small flag — no button chrome at all, on
   hover either (it sits among borderless emoji-icon buttons); like them it
   rests dimmed and brightens on hover. */
.lang-current {
  display: flex;
  align-items: center;
  padding: 2px;
  background: none;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0.7;
}

.lang-current:hover,
.lang-current.open {
  opacity: 1;
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

/* em-sized so the chip matches the surrounding text/icon size in each
   context; the hairline border delineates white-flagged countries (not
   button chrome). */
.flag {
  display: inline-flex;
  width: 1.5em;
  height: 1em;
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
