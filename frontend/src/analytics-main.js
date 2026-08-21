// Analytics page entry: mounts AnalyticsView inside the normal page layout.
// The backend renders #analytics-app inside #main and links this module for
// the initial load; pagerite.js also imports it on fetch-navigation to /_a.
import { createApp } from 'vue'
import AnalyticsView from './AnalyticsView.vue'

let app = null

export function mount(container) {
  if (app) return
  app = createApp(AnalyticsView, {
    initialRange: location.hash.slice(1) || 'week',
  })
  app.mount(container)
}

export function unmount() {
  app?.unmount()
  app = null
}

// Auto-mount on a normal (non-fetch) page load.
const container = document.getElementById('analytics-app')
if (container) mount(container)
