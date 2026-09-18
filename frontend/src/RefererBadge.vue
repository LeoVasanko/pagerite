<script setup>
// Referer + UTM as one badge in the analytics visit/crawler tables: the
// referer's favicon flush on the left, its host as the link text, then the
// UTM summary smaller/muted inside the same badge. Only the favicon and
// host are the link (external, new tab) — everything else, padding and
// UTM text included, copies the full utm tag list to the clipboard. The
// link is display: contents so its parts lay out as badge flex items.
// The badge carries a single one-fact-per-line tooltip (badge.title:
// origin, then each utm pair) — no titles on the inner elements.
// Referers are external, so there is no close event.
import { computed } from 'vue'
import { copyList } from './analytics/format.js'

const props = defineProps({
  badge: { type: Object, required: true },
  favicons: { type: Object, default: null },
})

const favicon = computed(() => (props.badge.origin ? props.favicons?.[props.badge.origin] : null))
</script>

<template>
  <span class="referer-badge"
        :class="{ 'with-icon': favicon, copyable: badge.utm }"
        :title="badge.title"
        @click="badge.utm && copyList(badge.utmCopy, $event)">
    <a v-if="badge.href" class="badge-link" :href="badge.href"
       target="_blank" rel="noopener" @click.stop>
      <img v-if="favicon" class="badge-favicon" :src="favicon" alt="" />
      <span v-if="badge.label">{{ badge.label }}</span>
    </a>
    <template v-else>
      <img v-if="favicon" class="badge-favicon" :src="favicon" alt="" />
      <span v-if="badge.label">{{ badge.label }}</span>
    </template>
    <small v-if="badge.utm" class="small">{{ badge.utm }}</small>
  </span>
</template>

<style scoped>
/* Browser-chrome chip on a translucent neutral wash (--badge-* in
   pagerite.css, deliberately unthemed): black-on-transparent and
   white-on-transparent favicons both stay legible on it. Colors go on the
   inner elements, so the theme's link color rules cannot cascade in. The
   padding is matched by negative margins so the chip's content stays
   exactly where the bare text would sit without the badge — except on the
   right, which keeps a small positive margin so the next trail item does
   not abut the chip. */
.referer-badge {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0.35em;
  /* Fixed line-height: the bar height is then exactly 1.2em + padding =
     1.6em, so the icon below can be sized to match precisely (an
     absolutely positioned replaced element cannot derive its height from
     top/bottom offsets — its aspect ratio wins and bottom is dropped). */
  line-height: 1.2;
  padding: 0.2em 0.5em;
  margin: -0.2em 0.25em -0.2em -0.2em;
  /* Fully rounded: the bar is exactly 1.6em tall, so a 0.8em radius makes
     both ends semicircles — a pill, with a full circle around the favicon
     on the left. */
  border-radius: 0.8em;
  background: var(--badge-bg);
  color: var(--badge-text);
  white-space: nowrap;
}

/* Room for the absolutely positioned icon (its 1.6em width plus the gap). */
.referer-badge.with-icon {
  padding-left: 1.95em;
}

/* Exactly the bar's height (1.6em, see line-height above), flush to the
   top/left/bottom borders. The badge does not clip it (no overflow:
   hidden): the icon may stick out past the bar's rounded corners.
   Absolute on purpose: an in-flow image is the flex
   container's first item and would supply the badge's baseline (an image's
   baseline is its bottom edge), pushing the badge text above the baseline
   of the trail items that follow. Out of flow, the badge's baseline comes
   from its text, so baselines match. */
.badge-favicon {
  position: absolute;
  top: 0;
  left: 0;
  width: 1.6em;
  height: 1.6em;
  object-fit: cover;
}

/* The link is display: contents: the favicon and label lay out as flex
   items of the badge itself, and only their actual boxes are clickable. */
.badge-link {
  display: contents;
}

/* Click-to-copy affordance on everything outside the link (the UTM text
   and the surrounding padding). */
.referer-badge.copyable {
  cursor: pointer;
}

.referer-badge span,
.referer-badge small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.referer-badge span { color: var(--badge-text); }
.referer-badge small { color: var(--badge-muted); }
</style>
