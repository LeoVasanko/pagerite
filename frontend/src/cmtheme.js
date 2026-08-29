// Shared CodeMirror styling for both editors: a light theme matching the
// site's inputs, and a highlight style in the site palette — the default
// highlight style (from basicSetup) underlines headings/links and uses
// colors that clash with the page.
import { EditorView } from 'codemirror'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags } from '@lezer/highlight'

// The base theme sets monospace on .cm-scroller, so the font must be set
// there, not on "&".
const cmEditorTheme = EditorView.theme({
  "&": {
    backgroundColor: "var(--bg)",
    color: "var(--text)",
  },
  ".cm-scroller": { fontFamily: '"Fira Code", monospace' },
  // Fira Code in CodeMirror: set the font on .cm-content (not only the
  // scroller) and force every span inside to inherit it, so highlighting
  // spans can't drift to a different font/metrics. Ligatures are disabled
  // entirely — CodeMirror measures per character, and ligature glyphs
  // render wider than the measured sum of their parts.
  ".cm-content": {
    caretColor: "var(--text)",
    fontFamily: '"Fira Code", monospace',
    fontVariantLigatures: "none",
    fontFeatureSettings: '"calt" 0',
    letterSpacing: "normal",
  },
  ".cm-content *": {
    fontFamily: "inherit",
    letterSpacing: "inherit",
  },
  ".cm-cursor": { borderLeftColor: "var(--text)" },
  // basicSetup's active-line highlight assumes a dark theme.
  ".cm-activeLine": { backgroundColor: "transparent" },
  "&.cm-focused": { outline: "none" },
})

// Selection color needs a baseTheme: only base themes support the
// &light/&dark selectors, and @codemirror/view's own selection rules use
// them — we must match its selectors exactly (equal specificity) and rely
// on mounting later to win. Focused: the page's --selection-bg (the base
// accents tint; themes may override it). Unfocused: hidden, like a normal
// input (CodeMirror greys it by default).
const cmSelection = EditorView.baseTheme({
  "&light .cm-selectionBackground, &dark .cm-selectionBackground":
    { backgroundColor: "transparent" },
  "&light.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, &dark.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground":
    { backgroundColor: "var(--selection-bg)" },
})

// Exported as one extension so the editors just list `cmTheme`.
export const cmTheme = [cmEditorTheme, cmSelection]

export const cmHighlight = syntaxHighlighting(HighlightStyle.define([
  { tag: tags.heading, fontWeight: "600", color: "var(--accent)" },
  { tag: tags.strong, fontWeight: "700" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.strikethrough, textDecoration: "line-through" },
  { tag: tags.link, color: "var(--accent2)" },
  { tag: tags.url, color: "var(--muted)" },
  { tag: tags.monospace, color: "var(--accent2)" },
  { tag: tags.quote, color: "var(--muted)", fontStyle: "italic" },
  // HTML (banner editor) and Markdown raw blocks
  { tag: tags.tagName, color: "var(--accent)" },
  { tag: tags.attributeName, color: "var(--accent2)" },
  { tag: tags.attributeValue, color: "var(--text)" },
  { tag: tags.comment, color: "var(--muted)" },
  { tag: tags.processingInstruction, color: "var(--muted)" },
]))
