// Shared CodeMirror styling for both editors: a light theme matching the
// site's inputs, and a highlight style in the site palette — the default
// highlight style (from basicSetup) underlines headings/links and uses
// colors that clash with the page.
import { EditorView } from 'codemirror'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags } from '@lezer/highlight'

// The base theme sets monospace on .cm-scroller, so the font must be set
// there, not on "&".
export const cmTheme = EditorView.theme({
  "&": {
    backgroundColor: "var(--bg)",
    color: "var(--text)",
  },
  ".cm-scroller": { fontFamily: '"Fira Code", monospace' },
  ".cm-content": { caretColor: "var(--text)" },
  ".cm-cursor": { borderLeftColor: "var(--text)" },
  // basicSetup's active-line highlight assumes a dark theme.
  ".cm-activeLine": { backgroundColor: "transparent" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground":
    { backgroundColor: "var(--line)" },
  "&.cm-focused": { outline: "none" },
})

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
