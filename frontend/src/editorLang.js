// The editor shell's shared language selection ('' = the primary language):
// backed by the app-wide store (./store), so the editor tabs' LangSelects
// and the public corner selector bind the same value. Linked to the
// whole-page language: while the panel is open it drives the page preview
// (EditorShell applies it as the fetch-time language override, swapdoc).
import { computed, ref } from 'vue'
import { pinia, useStore } from './store'

export const editorLang = computed({
  get: () => useStore(pinia).lang,
  set: (v) => { useStore(pinia).lang = v },
})

// The CURRENT PAGE's primary language ('' = not yet learned): the shell's
// settings fetch fills it with the site default; the page/structure tabs
// then refine it per page (doc accept / tree rows — strictly better
// sources, so they overwrite freely while the settings fetch only fills
// the unknown). EditorShell pins the preview by it when the selection is
// '' (the primary).
export const pagePrimary = ref('')
