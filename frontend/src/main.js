// Pagerite editor entries. Two separate apps, mounted in their own
// dynamically created host divs inside the static document:
// - PageEditor ("page" mode): pen next to an article heading — Markdown
//   editing with the preview rendered into the visible article.
// - SiteEditor ("site" mode): pen on the banner — banner HTML editing
//   (previewed into the real banner) and the site structure tree.
// The standalone /admin shell (#app in the DOM) mounts PageEditor with the
// page selected by location hash, as a no-dynamic-import fallback.
import { createApp } from 'vue'
import PageEditor from './PageEditor.vue'
import SiteEditor from './SiteEditor.vue'

let host = null

export function openEditor(path, { standalone = false, mode = 'page' } = {}) {
  closeEditor()
  host = document.createElement('div')
  host.className = 'editor-host'
  // Docked inside #content: below the banner, next to the article only.
  const container = (!standalone && document.getElementById('content')) || document.body
  container.prepend(host)
  if (!standalone) {
    document.body.classList.add('editing')
    // Which kind of editor is open; pagerite.js uses this to decide
    // whether a pen click closes the panel or swaps in the other editor.
    document.body.dataset.editorMode = mode
  }
  createApp(mode === 'site' ? SiteEditor : PageEditor, {
    pagePath: path,
    standalone,
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

const shell = document.getElementById('app')
if (shell) {
  // Standalone /admin shell: mount into it and follow the location hash.
  host = shell
  createApp(PageEditor, {
    pagePath: location.hash.replace(/^#\/?/, '').replace(/\/$/, ''),
    standalone: true,
    onClose: () => {},
  }).mount(shell)
}
