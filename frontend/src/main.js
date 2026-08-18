// Pagerite editor entries. Two separate apps, mounted in their own
// dynamically created host divs inside the static document:
// - PageEditor ("page" mode): pen next to an article heading — Markdown
//   editing with the preview rendered into the visible article.
// - SiteEditor ("site" mode): pen on the banner — banner HTML editing
//   (previewed into the real banner) and the site structure tree.
if (import.meta.env.DEV) {
  // Base styles only; the theme CSS is imported by pagerite.js (which
  // always runs first — the editor opens from public pages).
  import("./assets/pagerite.css");
}

import { createApp } from 'vue'
import PageEditor from './PageEditor.vue'
import SiteEditor from './SiteEditor.vue'

let host = null
let app = null
let savedTitle = null

export function openEditor(path, { mode = 'page' } = {}) {
  closeEditor()
  savedTitle = document.title
  host = document.createElement('div')
  host.className = 'editor-host'
  // Docked inside #content: below the banner, next to the article only.
  document.getElementById('content').prepend(host)
  document.body.classList.add('editing')
  // Which kind of editor is open; pagerite.js uses this to decide
  // whether a pen click closes the panel or swaps in the other editor.
  document.body.dataset.editorMode = mode
  app = createApp(mode === 'site' ? SiteEditor : PageEditor, {
    pagePath: path,
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

export function closeEditor() {
  if (!host) return
  document.body.classList.remove('editing')
  delete document.body.dataset.editorMode
  // Slide the panel out in sync with the page shifting back.
  host.firstElementChild?.classList.add('closing')
  const old = host
  const oldApp = app
  const restoreTitle = savedTitle
  host = null
  app = null
  savedTitle = null
  setTimeout(() => {
    oldApp?.unmount()
    old.remove()
    if (host) return // a new editor has opened; its title is authoritative
    // Restore the server-rendered title for the current URL. Re-fetching makes
    // sure a brand change in the site editor or an in-place navigation leaves
    // the correct public title behind.
    fetch(location.pathname)
      .then((r) => r.text())
      .then((html) => {
        const doc = new DOMParser().parseFromString(html, 'text/html')
        if (doc.title) document.title = doc.title
      })
      .catch(() => {
        if (restoreTitle != null) document.title = restoreTitle
      })
  }, 250)
}
