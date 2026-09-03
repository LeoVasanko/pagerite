// The editor shell's shared language selection ('' = the primary language):
// one state, v-modeled by the LangSelect of every tab that has one (page,
// structure). While the panel is open it also drives the page preview —
// EditorShell applies it as the fetch-time language override (swapdoc).
import { ref } from 'vue'

export const editorLang = ref('')

// The CURRENT PAGE's primary language ('' = not yet learned): the shell's
// settings fetch fills it with the site default; the page/structure tabs
// then refine it per page (doc accept / tree rows — strictly better
// sources, so they overwrite freely while the settings fetch only fills
// the unknown). EditorShell pins the preview by it when the selection is
// '' (the primary).
export const pagePrimary = ref('')
