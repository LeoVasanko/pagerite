// The app's shared Pinia store — cross-bundle UI state lives here. Every
// entry chunk imports its own copy of this module, so the Pinia instance
// is parked on window (Vue itself is a shared chunk, so reactivity works
// across the copies). Pass `pinia` explicitly when calling useStore
// outside a component (module code, no active instance).
import { createPinia, defineStore } from 'pinia'

export const pinia = (window.__pageritePinia ??= createPinia())

export const useStore = defineStore('pagerite', {
  state: () => ({
    // The ONE language selection, v-modeled by both dropdowns (editor
    // tabs, public corner selector): '' = no explicit pick (the page's
    // primary / autodetect), else a concrete tag. A pick from either
    // dropdown is visible to everyone immediately.
    lang: '',
    // The language the current page was actually served in (set by
    // pagerite.js per navigation) — the selector's highlight fallback
    // when there is no explicit pick.
    servedLang: '',
    // The public selector's page data: hreflang alternates
    // ([{tag, href, primary}]) and whether to show at all.
    langAlternates: [],
    langSelectorActive: false,
  }),
})
