// Language helpers shared by the editors (the PageEditor language picker,
// the localization settings tab). Flags come from the country-flag-icons
// set, same as the analytics visitor cells.
import * as flagSvgs from 'country-flag-icons/string/3x2'

// The Seed-X reference translator's languages (scripts/translator.py) — the
// translation-target ceiling — each mapped to the language's home country
// flag (England for English, Portugal for Portuguese — not the most
// populous variant). Internal tags are the bare 2-letter base subtags; the
// translator decides the variant. A variant tag (en-US, pt-BR) is still a
// valid explicit selection for a future translator that distinguishes them
// — flagFor shows its own region then.
export const TRANSLATABLE = {
  ar: 'EG', cs: 'CZ', da: 'DK', de: 'DE', el: 'GR', en: 'GB', es: 'ES',
  fa: 'IR', fi: 'FI', fr: 'FR', hu: 'HU', id: 'ID', it: 'IT', ja: 'JP',
  ko: 'KR', ms: 'MY', nl: 'NL', no: 'NO', pl: 'PL', pt: 'PT', ro: 'RO',
  ru: 'RU', sv: 'SE', th: 'TH', tr: 'TR', uk: 'UA', vi: 'VN', zh: 'CN',
}

// The languages in geographic/cultural groups (the lang tab's flag grid
// lays them out one group per row, in this order): English with the
// Nordics, then Western/Central and Eastern Europe, Southern Europe with
// the Middle East, and Asia.
export const LANG_GROUPS = [
  ['en', 'nl', 'da', 'no', 'sv', 'fi', 'ru'],
  ['fr', 'de', 'pl', 'cs', 'hu', 'ro', 'uk'],
  ['es', 'pt', 'it', 'el', 'tr', 'ar', 'fa'],
  ['zh', 'ja', 'ko', 'vi', 'th', 'id', 'ms'],
]

const displayNames = new Intl.DisplayNames(['en'], { type: 'language' })

// English display name for a language tag ("fi" -> "Finnish").
export function langName(tag) {
  try {
    return displayNames.of(tag) || tag
  } catch {
    return tag
  }
}

// Flag SVG string for a language tag: an explicit region variant (en-US)
// gets its own region's flag; a bare base tag maps to the language's home
// country (en → GB, pt → PT); languages outside the list fall back to the
// tag's most likely region.
export function flagFor(tag) {
  tag = tag || ''
  if (!tag.includes('-')) {
    const country = TRANSLATABLE[tag.split('-')[0].toLowerCase()]
    if (country) return flagSvgs[country] || ''
  }
  try {
    return flagSvgs[new Intl.Locale(tag).maximize().region] || ''
  } catch {
    return ''
  }
}
