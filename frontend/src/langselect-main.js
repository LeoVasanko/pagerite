// Public language-selector entry: imported on demand by pagerite.js on
// pages advertising more than one language in their hreflang alternates.
// Vue, Pinia and the flag SVG set live in this chunk only — untranslated
// pages never pay for them. The selector's state lives in the shared
// store (./store), not the DOM: the corner container is rebuilt freely
// and ensureMounted re-mounts from the store.
import { createApp } from 'vue'
import LangSelector from './LangSelector.vue'
import { pinia, useStore } from './store'

let app = null

function store() {
  return useStore(pinia)
}

// The current page's languages (called on every navigation).
export function setLanguages(alternates, current) {
  Object.assign(store(), {
    langAlternates: alternates,
    servedLang: current,
    langSelectorActive: true,
  })
}

// The current page is single-language: the selector goes away.
export function hide() {
  store().langSelectorActive = false
  app?.unmount()
  app = null
}

// Mount the selector as the container's first item; re-mount when its
// element went away with a container rebuild (a live app updates from the
// store reactively).
export function ensureMounted(host) {
  if (!store().langSelectorActive || !host) {
    app?.unmount()
    app = null
    return
  }
  if (app && host.contains(app._container)) return
  app?.unmount()
  const el = document.createElement('div')
  el.id = 'lang-selector'
  host.prepend(el)
  app = createApp(LangSelector)
  app.use(pinia)
  app.mount(el)
}
