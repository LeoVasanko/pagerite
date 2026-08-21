<script setup>
// Visitor metadata cell shared by the recent-visits and crawlers tables.
// Displays IP/network/host, country flag/city, UA, and language when available.
// Clicking the IP copies the full address to the clipboard.
import { computed } from 'vue'
import * as flagSvgs from 'country-flag-icons/string/3x2'
import { copyIp, formatLang } from './analytics/format.js'

const props = defineProps({
  ip: { type: String, default: '' },
  ipDisplay: { type: String, default: '—' },
  ua: { type: String, default: '' },
  uaRaw: { type: String, default: '' },
  country: { type: String, default: '' },
  city: { type: String, default: '' },
  lang: { type: String, default: '' },
  langDisplay: { type: String, default: '' },
  isHost: { type: Boolean, default: false },
})

const hasCountry = computed(() => !!(props.country && props.country !== '—'))
const hasCity = computed(() => !!(props.city && props.city !== '—'))
const hasLocale = computed(() => hasCountry.value || hasCity.value)
const langValue = computed(() => props.langDisplay || formatLang(props.lang))
const showLang = computed(() => langValue.value && langValue.value !== '—')

function flagSvg(code) {
  return flagSvgs[code?.toUpperCase()] || ''
}

function countryName(code) {
  if (!code) return ''
  try {
    return new Intl.DisplayNames(['en'], { type: 'region' }).of(code.toUpperCase())
  } catch {
    return ''
  }
}
</script>

<template>
  <td class="visitor-cell" :class="{ 'host-cell': isHost }">
    <div class="visitor-rows">
      <div class="visitor-row">
        <div class="locale-line">
          <span v-if="flagSvg(country)" class="flag" v-html="flagSvg(country)" :title="countryName(country) || country"></span>
          <template v-if="hasCity"><small class="city-name muted">{{ city }}</small></template>
          <template v-else-if="!hasLocale">—</template>
        </div>
        <div class="ip-line">
          <span class="clickable-ip small muted"
                :title="ip"
                @click="copyIp(ip, $event)">{{ ipDisplay }}</span>
        </div>
      </div>
      <div class="visitor-row">
        <div class="ua-line"><small class="muted" :title="uaRaw">{{ ua || '—' }}</small></div>
        <div v-if="showLang" class="locale-lang"><small class="muted">{{ langValue }}</small></div>
      </div>
    </div>
  </td>
</template>

<style scoped>
.visitor-cell {
  width: 18em;
  max-width: 18em;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: top;
}

.visitor-cell.host-cell {
  text-align: right;
}

.visitor-rows {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.visitor-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.visitor-row > * {
  min-width: 0;
}

.locale-line,
.ip-line,
.ua-line {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.locale-line {
  text-align: left;
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.ip-line {
  text-align: right;
}

.ua-line {
  text-align: left;
}

.locale-lang {
  flex: 0 0 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: right;
}

.city-name {
  display: inline-block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}

.flag {
  display: inline-flex;
  width: 18px;
  height: 12px;
  border-radius: 2px;
  overflow: hidden;
  border: 1px solid var(--line);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.2) inset;
  vertical-align: middle;
}

.flag :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}
</style>
