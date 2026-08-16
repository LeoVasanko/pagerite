// Pagerite editor entries. Two separate apps, mounted in their own
// dynamically created host divs inside the static document:
// - PageEditor ("page" mode): pen next to an article heading — Markdown
//   editing with the preview rendered into the visible article.
// - SiteEditor ("site" mode): pen on the banner — banner HTML editing
//   (previewed into the real banner) and the site structure tree.
if (import.meta.env.DEV) {
  import("./assets/pagerite.css");
  import("./assets/themes/purple/theme.css");
}

import { createApp } from 'vue'
import PageEditor from './PageEditor.vue'
import SiteEditor from './SiteEditor.vue'

let host = null

export function openEditor(path, { mode = 'page' } = {}) {
  closeEditor()
  host = document.createElement('div')
  host.className = 'editor-host'
  // Docked inside #content: below the banner, next to the article only.
  document.getElementById('content').prepend(host)
  document.body.classList.add('editing')
  // Which kind of editor is open; pagerite.js uses this to decide
  // whether a pen click closes the panel or swaps in the other editor.
  document.body.dataset.editorMode = mode
  createApp(mode === 'site' ? SiteEditor : PageEditor, {
    pagePath: path,
    onClose: closeEditor,
  }).mount(host)
}

export function closeEditor() {
  if (!host) return
  document.body.classList.remove('editing')
  delete document.body.dataset.editorMode
  // Slide the panel out in sync with the page shifting back.
  host.firstElementChild?.classList.add('closing')
  const old = host
  host = null
  setTimeout(() => old.remove(), 250)
}
