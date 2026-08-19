// Pagerite editor entry. A single tabbed EditorShell is mounted in a
// dynamically created host div inside the static document. The shell hosts
// four tabs: PageEditor (Markdown + preview), BannerEditor (per-page banner
// HTML/design), SiteEditor (site-wide config), and StructureEditor (the
// site structure tree).
//
// Individual pens are shorthands that open the shell on a particular tab;
// once the shell is open, pens switch tabs instead of closing/remounting.
// Closing hides the shell but keeps the Vue app mounted, so editor state —
// including unsaved page text — survives and editing can continue on the
// next pen click; it is lost only on a real page reload.
if (import.meta.env.DEV) {
  // Base styles only; the theme CSS is imported by pagerite.js (which
  // always runs first — the editor opens from public pages).
  import("./assets/pagerite.css");
}

import { createApp } from 'vue'
import EditorShell from './EditorShell.vue'

let host = null
let app = null
let savedTitle = null
let visible = false

export function openEditor(path, { mode = 'page' } = {}) {
  if (app) {
    // Shell already mounted: re-show it if hidden, switch to the requested
    // tab and retarget the editors to the current page (the shell survives
    // fetch-navigation).
    if (!visible) showEditor()
    document.body.dataset.editorMode = mode
    dispatchEvent(new CustomEvent('pagerite:switch-editor', { detail: { mode, path } }))
    return
  }
  savedTitle = document.title
  host = document.createElement('div')
  host.className = 'editor-host'
  // Docked inside #content: below the banner, next to the article only.
  document.getElementById('content').prepend(host)
  document.body.classList.add('editing')
  // Which tab is active; pagerite.js uses this to decide whether a pen click
  // closes the panel or switches tabs.
  document.body.dataset.editorMode = mode
  visible = true
  app = createApp(EditorShell, {
    pagePath: path,
    initialMode: mode,
    onClose: closeEditor,
  })
  app.mount(host)
  // The slide-in (editor-slide-in in pagerite.css) is a one-shot open
  // effect; once finished, drop it so that later stylesheet swaps (theme
  // change re-creating @keyframes) cannot restart it.
  const root = host.firstElementChild
  root?.addEventListener('animationend', function done(e) {
    if (e.target !== root) return
    root.removeEventListener('animationend', done)
    root.style.animation = 'none'
  })
}

function showEditor() {
  savedTitle = document.title
  host.style.display = ''
  host.firstElementChild?.classList.remove('closing')
  document.body.classList.add('editing')
  visible = true
  dispatchEvent(new CustomEvent('pagerite:editor-shown'))
}

export function closeEditor() {
  if (!visible) return
  visible = false
  document.body.classList.remove('editing')
  // dataset.editorMode is kept while hidden: the tabs use it to tell whether
  // a pagerite:editor-shown event targets them.
  // Slide the panel out in sync with the page shifting back, then hide it.
  host.firstElementChild?.classList.add('closing')
  const h = host
  setTimeout(() => { h.style.display = 'none' }, 250)
  dispatchEvent(new CustomEvent('pagerite:editor-hidden'))
  // Restore the server-rendered title for the current URL. Re-fetching makes
  // sure a brand change in the site editor or an in-place navigation leaves
  // the correct public title behind.
  const restoreTitle = savedTitle
  savedTitle = null
  fetch(location.pathname)
    .then((r) => r.text())
    .then((html) => {
      if (visible) return // reopened meanwhile; the editor owns the title
      const doc = new DOMParser().parseFromString(html, 'text/html')
      if (doc.title) document.title = doc.title
    })
    .catch(() => {
      if (!visible && restoreTitle != null) document.title = restoreTitle
    })
}
