// Slugs are URL path segments: lowercase ASCII letters, digits, hyphens.
// The transliteration package folds unicode (incl. asian scripts) to
// ASCII and spaces to hyphens; we then drop everything outside the slug
// charset (including the reserved leading "_"/".", slashes, backslashes,
// dots).
import { slugify as transliterate } from 'transliteration'

export function slugify(s) {
  return transliterate(s).replace(/[^a-z0-9-]/g, '').replace(/-{2,}/g, '-')
}
