// Analytics page entry: mounts AnalyticsView inside the normal page layout.
// In production the backend inlines this module into the /_a page (and
// pagerite.js re-creates the script element after fetch-navigations there);
// in dev pagerite.js imports it from the Vite dev server on demand. Either
// way it auto-mounts on #analytics-app when it evaluates, and unmounts when
// pagerite.js announces a swap away from /_a.
import { createApp } from 'vue'
import AnalyticsView from './AnalyticsView.vue'

let app = null

export function mount(container) {
  if (app) return
  app = createApp(AnalyticsView)
  app.mount(container)
}

export function unmount() {
  app?.unmount()
  app = null
}

// pagerite.js calls this before swapping away from /_a; each evaluation
// (the inlined production module evaluates fresh on every visit) replaces
// the handle.
window.__pageriteAnalyticsUnmount = unmount

// Auto-mount when the page holding #analytics-app is present.
const container = document.getElementById('analytics-app')
if (container) mount(container)
