<script setup>
// Referer + UTM as one badge in the analytics visit/crawler tables: the
// referer's favicon flush on the left, its host as the link text, then the
// UTM summary smaller/muted inside the same badge. The whole badge links to
// the referer origin (external, new tab) and carries a single one-fact-
// per-line tooltip (badge.title: origin, then each utm pair) — no titles on
// the inner elements. Referers are external, so there is no close event.
import { computed } from 'vue'

const props = defineProps({
  badge: { type: Object, required: true },
  favicons: { type: Object, default: null },
})

const favicon = computed(() => (props.badge.origin ? props.favicons?.[props.badge.origin] : null))
</script>

<template>
  <a class="referer-badge"
     :href="badge.href || undefined"
     :title="badge.title"
     :target="badge.href ? '_blank' : undefined"
     :rel="badge.href ? 'noopener' : undefined">
    <img v-if="favicon" class="badge-favicon" :src="favicon" alt="" />
    <span v-if="badge.label">{{ badge.label }}</span>
    <small v-if="badge.utm" class="small">{{ badge.utm }}</small>
  </a>
</template>

<style scoped>
/* Browser-chrome chip on a fixed neutral palette (--badge-* in
   pagerite.css, deliberately unthemed): black-on-transparent and
   white-on-transparent favicons both stay legible on it, and the text is
   always dark regardless of the theme's link/text colors. Colors go on the
   inner elements, so the theme's a / a:hover color rules (which target the
   anchor) cannot cascade in. */
.referer-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.35em;
  padding-right: 0.45em;
  border-radius: 0.25rem;
  background: var(--badge-bg);
  color: var(--badge-text);
  white-space: nowrap;
  overflow: hidden;
}

/* Flush with the badge's top/left/bottom edges: a full-height square (the
   badge has no padding on those sides), corners clipped by the badge's
   overflow: hidden border-radius. */
.badge-favicon {
  width: 1.5em;
  height: 1.5em;
  object-fit: cover;
  flex: none;
}

.referer-badge > span,
.referer-badge > small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.referer-badge > span { color: var(--badge-text); }
.referer-badge > small { color: var(--badge-muted); }
</style>
