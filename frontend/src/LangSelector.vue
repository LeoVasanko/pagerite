<script setup>
// The public page's language selector: the editors' flag dropdown
// (LangSelect) as the first item of the banner's corner container, fed
// from the shared store (pagerite.js sets the page's hreflang alternates
// and served language per navigation). It binds the same store.lang the
// editor's dropdown binds, so both always show the same selection. A pick
// also dispatches pagerite:set-session-lang — pagerite.js swaps the page
// in place when the editor is closed (open, the editor reacts to the
// store and re-renders it).
import { computed } from 'vue'
import LangSelect from './LangSelect.vue'
import { flagFor, langName } from './langs'
import { useStore } from './store'

const store = useStore()

// The "(primary)" marker is admin-panel information; the public selector
// lists plain languages.
const options = computed(() =>
  store.langAlternates.map((a) => ({
    tag: a.tag,
    code: a.tag,
    name: langName(a.tag),
    flag: flagFor(a.tag),
    primary: false,
  })),
)
const primaryTag = computed(() => store.langAlternates.find((a) => a.primary)?.tag ?? '')
// The explicit pick, else the served language (header-autodetected pages
// may have neither), else the primary.
const model = computed(() => store.lang || store.servedLang || primaryTag.value)

function go(tag) {
  store.lang = tag === primaryTag.value ? '' : tag
  dispatchEvent(new CustomEvent('pagerite:set-session-lang', { detail: { lang: tag } }))
}
</script>

<template>
  <LangSelect :model-value="model" :options="options" @update:model-value="go" />
</template>
