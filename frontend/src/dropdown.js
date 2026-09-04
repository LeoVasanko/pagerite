// Shared popup open-state behavior: while `open` (a ref, truthy = open)
// is set, a pointerdown outside `root` (a template ref covering both the
// toggle button and the popup) or Escape resets it to null. One logic for
// every dropdown (LangSelect, the page editor's class/table pickers), so
// they can't drift apart.
import { onBeforeUnmount, watch } from 'vue'

export function usePopup(open, root) {
  let off = null
  const stop = watch(open, (v) => {
    off?.()
    off = null
    if (!v) return
    const down = (ev) => { if (!root.value?.contains(ev.target)) open.value = null }
    const key = (ev) => { if (ev.key === 'Escape') open.value = null }
    addEventListener('pointerdown', down, true)
    addEventListener('keydown', key)
    off = () => {
      removeEventListener('pointerdown', down, true)
      removeEventListener('keydown', key)
    }
  })
  onBeforeUnmount(() => { off?.(); stop() })
}
