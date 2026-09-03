# Localization

Pages are served in the visitor's language based on a `?lang=` query
parameter or the `Accept-Language` header.

- **Phase 1 (implemented):** negotiation, URL scheme, caching, rendering
  plumbing. Translations are consumed through a stub interface; the database
  still holds only the original language.
- **Phase 2 (implemented):** gettext-style fragment storage in the
  database — machine-translated chunks plus user override patches, assembled
  at render time. Storage details in `docs/migrate.md`.

## Phase 1: negotiation and URLs

### The primary language

Each article has a primary (original) language: `Node.language`, inherited
down the tree like `banner` — "" = the nearest ancestor's, the front page
last (it doubles as the site default), with `en` as the final fallback
(`ORIGINAL_LANGUAGE`, `primary_lang()` in `pagerite/i18n.py`). It is
configured per row in the structure editor. Everything per-article keys
off the resolved value: language selection, `<html lang>`, canonical URLs,
what counts as a translation, and the translation targets (a node's own
primary is never one — so the target set may include the site default, and
a page in another language can be translated into it).

### Language selection

Deliberately simple — **q-values are ignored**:

- All known `Accept-Language` implementations send the header **in order of
  preference**, so we parse it as an ordered list and never reorder.
- Selection rule (`select_language` in `pagerite/i18n.py`):
  1. If `?lang=<tag>` is present, use it (if a translation exists; otherwise
     fall through to header logic).
  2. If the article's original language appears anywhere in the header list,
     use the **original**. Rationale: an AI translation is strictly worse
     than the original for anyone who has that language configured at all
     (e.g. `fi-FI, fi, en-US, en` gets English, not machine-translated
     Finnish).
  3. Otherwise walk the header list in order and use the first language for
     which a translation exists.
  4. Fall back to the original.

Region tags normalize to their base subtag (`fi-FI` → `fi`).

### URLs: pretty for users, indexable for search engines

- Canonical URLs stay pretty (`/some-page`). Each language version is
  addressable as `/some-page?lang=fi` so search engines can index them.
- `<link rel="canonical">` names the **actually served language**: the plain
  URL when serving the original (for SEO the non-query URL means the
  article's own language), `?lang=xx` when serving a translation — however
  the language was arrived at (query or header).
- `<link rel="alternate" hreflang="…">` entries follow the canonical
  directly (before the social meta tags) and are the same set on every
  page — the site-wide configured languages (`translate_langs`, which the
  translator works to fill in): `x-default` first, pointing at the plain
  autodetecting URL, then every language explicitly with `?lang=`, the
  page's own primary language included.
- The override sticks for the session of clicks: a page requested with
  `?lang=` replicates the query onto the navigation links it renders (nav,
  sidebar, cards, brand — in-article links are content and stay as
  authored), so plain clicks and no-JS navigation keep the language.
  pagerite.js additionally strips the query from the address bar via
  `history.replaceState` (pretty, shareable URLs), remembers the language,
  and adds it to every internal fetch that lacks one (preloads,
  fetch-navigations, history traversals); history entries stay query-less.
- A full page refresh or a shared link resets to automatic selection (header
  only). This gives a clean one-time override without cookies.

### Response correctness

- Content responses carry `Vary: accept-language` (added to the existing
  `accept-encoding` vary).
- `_cached_body` and the page ETag include the **selected language** (not the
  raw header, which would blow up the cache key space) and the **replicated
  link language**: a `?lang=fi` render and a header-selected Finnish render
  of the same page differ in their navigation links, so they are cached as
  separate variants.
- `<html lang="…">` reflects the served language, and an RTL language
  (`i18n.RTL_LANGUAGES` — ar, fa, he, ur) also sets `dir="rtl"` on `<html>`
  (the editor panel carries its own `lang="en" dir="ltr"` so it stays LTR).
  Client-side page swaps (fetch navigation in pagerite.js, editor re-renders
  in swapdoc.js) copy both attributes from the fetched document, so a hot
  switch into or out of an RTL page flips the layout without a reload.

### Rendering

- The translated Markdown goes through the same `markdown.render` pipeline.
- Navigation/sidebar titles come from the translation's title map, with
  per-node fallback to the original title (a partially translated tree must
  still render).
- Category placeholder pages (the 404s for content-less labels) select a
  language like content pages, but over the **subtree's** combined
  availability (`subtree_languages`) — they have no chunks of their own;
  the heading, navigation and card text localize from the title map and
  the target articles' translations.
- Card descriptions and cover picks run on the target article's hybrid
  Markdown where that page is available in the served language, with
  per-card fallback to the original.
- Fixed UI strings ("Not Found" etc.) and the editor UI stay English for now.
- The markdown typographer (SmartyPants) is English-centric; per-language
  typographer options are a possible follow-up, not blocking.

## Phase 2: fragment-based translation storage (implemented)

Phase 1 assumed whole-page translated Markdown delivered from outside. The
refined model is gettext-style: an article has **one primary version** (its
`content`, in its own language) plus, per target language, **machine
fragments** (translated chunks of Markdown) and **user patches** (minimal
editor overrides). Both are stored in the database and assembled into the
served Markdown at render time.

### The scenario this must handle

1. Article written in English.
2. Machine-translated into Spanish → fragments stored.
3. Editor fixes one Spanish paragraph and changes a link elsewhere to point
   at a Spanish resource → user patch hunks stored.
4. English article edited → the edited chunk's key changes; its Spanish
   fragment no longer matches.
5. Page requested before the machine translation refreshes → served as a
   **hybrid**: old fragments for unchanged chunks, plain English for the
   edited chunk. User patches are attempted against this hybrid, best effort,
   each hunk independently: the text fix is stale (its search text no longer
   exists) and silently skipped; the link change still applies even though
   the link sits in the now-English paragraph.
6. Machine translation refreshes → full Spanish again, with both patch hunks
   applying.

### Chunks

`chunk_markdown(markdown)` splits the source into block-level chunks —
blank-line-separated blocks: headings, paragraphs, code fences (kept whole),
list blocks, tables, HTML blocks. A chunk's identity is its **source text**,
gettext-msgid style:

```python
chunk_key = blake3(normalize(chunk_text)).digest(9)  # bytes; base64 at the JSON level
```

(`normalize`: strip trailing whitespace per line, collapse surrounding blank
lines — so whitespace-only source edits don't invalidate translations.)

Consequences:

- Editing the English source invalidates exactly the edited chunks; all
  other fragments keep applying. Stale fragments are simply never referenced
  again and can be garbage-collected lazily (or left; they are tiny).
- No explicit "source version" bookkeeping is needed — staleness falls out
  of the keys.

### User patches

Editors always edit **full Markdown** in the existing editor UX — never
fragments. When editing a translated view (`?lang=es`), the editor is loaded
with the *current hybrid Markdown*; on save, the server computes a minimal
diff against that hybrid and stores it as a patch:

```python
class Patch(msgspec.Struct, omit_defaults=True):
    """One editing session's overrides, applied independently per hunk."""

    hunks: list[tuple[str, str]] = []  # (search, replace) on hybrid Markdown
```

Hunks are produced from `difflib.SequenceMatcher` on the hybrid vs. the
edited text at block granularity: each `replace`/`delete`/`insert` opcode
becomes one `(search, replace)` pair, with the preceding block's tail as
left context for `insert` (pure inserts have empty search context otherwise).
Application is dead simple:

```python
def apply_patch(hybrid: str, patch: Patch) -> str:
    for search, replace in patch.hunks:
        if search and search in hybrid:
            hybrid = hybrid.replace(search, replace, 1)
        # missing search text = stale hunk -> silently skipped
    return hybrid
```

Per-hunk independence is the robustness property from the scenario: a stale
text fix does not block a still-valid link change. Patches are stored as an
ordered list and applied in order.

### Storage

Full storage design and the `migrate_v3` restructuring live in
`docs/migrate.md`. The short version, as it concerns this document:

- Originals **and** translations are content-addressed text chunks in flat
  stores: `Data.chunks: dict[bytes, str]` and
  `Data.trans: dict[bytes, dict[str, str]]` (chunk hash → lang → text) —
  path-independent, so repeated paragraphs and menu titles are translated
  once and article moves touch nothing. `Node.chunks: list[bytes]` gives
  each article its order.
- `Node` gains **`language: str = ""`**, inherited down the tree like
  `banner` (empty = nearest ancestor, front page last, site default `en`
  final). `select_language` and `<html lang>` use the resolved value instead
  of the global `ORIGINAL_LANGUAGE` constant.
  - **Known weakness:** changing a page's (or subtree's) `language` after
    translations exist mis-keys everything — translations are keyed by
    *source* chunks, so old entries silently stop matching and user patches
    (searching for old-hybrid text) mostly go stale. That is acceptable:
    the orphaned data is harmless and translations regenerate. We do not
    migrate translations across a language change.
- Article paths are stored and keyed **without leading slashes**
  (`"docs/setup"`, front page `""`); slashes are added only in hrefs.

### Render pipeline (the phase-1 `get_translation` stub, now real)

```python
def get_translation(data, path, lang) -> Translation | None:
    if lang not in node.langs:
        return None
    hybrid = "\n\n".join(
        chunks[h] if h in node.no_trans else trans.get(h, {}).get(lang, chunks[h])
        for h in node.chunks
    )
    for patch in data.patches.get(f"{path}:{lang}", []):
        hybrid = apply_patch(hybrid, patch)
    return Translation(markdown=hybrid, titles=title_map(data, lang))
```

- Availability is an article-level index: `node.langs: dict[lang, True]`,
  maintained by the translation writers (translator job, patch saves) in the
  same transaction as their data writes — rendering and language selection
  never probe the `trans` store chunk by chunk. A stale key is benign (the
  "translation" just renders as the original).
- `titles` for nav/sidebar/cards: each node's translated title is
  `trans.get(hash(node.title), {}).get(lang)` with per-node fallback — one
  dict lookup per nav item at render time.
- Cache invalidation: writes to `chunks` / `trans` / `patches` (translator,
  editor saves) call `_invalidate_pages()`, same as content writes.

### Editor flow

The page and structure editors share one language selector (`LangSelect.vue`:
a small flag button opening a dropdown; the same country-flag-icons set as
the analytics visitor cells), v-modeled on one shell-wide selection
(`editorLang.js`, `''` = the primary language). The page editor lists the
page's own primary language (`Node.language`, resolved through the
hierarchy and echoed in the WS doc as `primary_lang`) plus the union of
the page's translations (`node.langs`) and
the site-wide `translate_langs`; it always opens in the primary language,
even when the page itself was served in a translation. A note under the
toolbar states the blast radius:
edits to the primary language re-chunk the original (invalidating the
affected translation fragments everywhere); edits to a translation stay
local to that language.

While the editor panel is open, its language selection **overrides the
normal language preferences** for the page preview: EditorShell pins every
in-place re-render and pagerite.js fetch/prefetch to it (`?lang=` — a
primary selection pins by the current page's own resolved primary, which
`select_language` honors), and closing the panel restores the normal
preferences.

- WS `open` with a `lang` returns the effective **hybrid** Markdown and
  title for that language (ungated by `node.langs` — a language without
  any fragments yet starts from the original text), plus the language
  metadata (`lang`, `primary_lang`, `langs`, `translate_langs`).
- The editor keeps a **shadow copy** of the Markdown it opened. WS `save`
  with `lang` sends it as `base`; the server diffs `base` → submitted text
  (`make_patch`) and appends a `Patch`. Diffing against the shadow (rather
  than the current hybrid) keeps hunks correct when the original or the
  machine translation moved under an open editor; application against the
  then-current hybrid stays best-effort per hunk, as designed.
- A changed **title** on a translated save becomes a fragment in
  `Data.trans` keyed by the original title's chunk hash — the same storage
  as machine title translations. An untouched title field (holding the
  served translation) is not sent, so saving never freezes a stale machine
  title into an override.
- Saving never deletes; a translation additionally cannot be emptied (that
  would render as a blank page in that language).
- The live preview renders the version being edited, whichever language
  the page itself was loaded in (the render is just the edited Markdown +
  title). A translated save keeps that preview in place — re-fetching the
  page would come back in the header-selected language.
- Saving the primary-language version re-chunks the submitted Markdown and
  updates `Data.chunks` / `node.chunks` — only genuinely new text lands in
  the kanta change diff (see docs/migrate.md).

The **structure editor** selects from the same languages with the same
`LangSelect` (the selection is shared — switching in either tab switches
both, and the preview). It is also where a page's **primary language** is
configured: each row carries a small flag dropdown (the resolved flag,
dimmed while inherited) that sets `Node.language` via a structure op —
'' = inherit, so setting it on a section covers the whole subtree. The
tree it
lists (`GET /_api/pages?lang=`) comes back with per-language titles where a
translation exists (`translated` marks those rows; untranslated rows show
the original title, dimmed). Retitling in a non-primary language posts the
structure op with a `lang` and writes a per-language title fragment in
`Data.trans` (keyed by the original title's chunk hash, exactly like a
machine title translation — a user edit simply overwrites it); sending the
original's text drops the override. The structure itself — slugs,
hierarchy, order — is language-independent, so pending rows, slug edits,
drag-and-drop and deletes work identically in every language.

### Translator service API

An external machine-translation service connects over WebSocket at
`/_translate/{key}` — deliberately **not** under `/_api`: the SSO
forward-auth does not cover that route, and the key in the path is the
access control. Keys live in `Data.translate_keys` (key -> display name) —
12 lowercase alphanumeric characters each; the first is generated at
database bootstrap, further ones are managed in the editor's lang tab
(add/rename/delete ride the `PUT /_api/settings` round-trip; the name is
an inline display label only). The full WS URL(s) are printed in the
startup log (`ws://localhost:{port}/_translate/{key}` locally,
`wss://{hostname}/_translate/{key}` on a public hostname) and shown in the
lang tab as click-to-copy links; the keys are also surfaced in
`GET /_api/settings` as `translate_keys`. An unknown or empty key rejects
the handshake (close-before-accept → HTTP 403). Transactions storing results record the connecting key as the kanta
transaction `user`.

Frames are JSON-encoded tagged msgspec structs (`pagerite/translate.py`;
`bytes` fields ride as base64):

- `{"type": "hello", "langs": [...]}` — client greeting announcing its
  **capabilities**: the language codes its model can produce (normalized
  to base subtags; `en`/empty dropped).
- `{"type": "job", "lang", "key", "texts", "path", "kind", "contexts"}` —
  server push: ONE fragment to translate (an article title or a chunk), as
  a list of **prose segments** (see Segmentation below). `contexts` is
  parallel to `texts` ("" = none): the surround to translate the segment
  in — for clients that translate better with context (see below).
  Contexts are not part of the result.
- `{"type": "result", "lang", "key", "texts"}` — client reply: the
  segments translated, same order and count, matching its job by (lang, key).

Which languages get translated is **server-configured**:
`Data.translate_langs` (presence-key dict, bootstrapped to Spanish and
Chinese — edited in the editor shell's localization tab, whose flag grid
lists every language including English, or set via `/_api/settings` as
`translate_langs`). A target equal to an article's own primary language is
skipped per article (its original already is that language), so the set
may freely contain the site default. The dispatcher offers a
connection jobs only in `wanted ∩ capable`; a connection without overlap
simply stays idle.

`DELETE /_api/translations` (the localization tab's "refresh all
translations" button) drops every machine translation (`Data.trans`) and
rebuilds the availability index (`node.langs`) from the surviving user
patches, so the dispatcher re-translates everything from scratch; the
run's validation skip-list is cleared with it, giving rejected fragments
another chance.

Dispatch semantics (the `Dispatcher` in `pagerite/translate.py`; api.py only
registers the route):

- **One job at a time per connection** — the next job is sent only after
  the current one's result. Clients wanting parallelism open multiple
  connections (e.g. several `scripts/translator.py` instances).
- Pending work is derived from the `trans` store
  (`translate.pending_items`) minus the items in flight on any connection,
  so a **disconnect requeues** that connection's in-flight item and it is
  offered to any free capable connection.
- Dispatch re-runs on every relevant event: Hello, result, disconnect and
  content change (`_invalidate_pages()` schedules it, so the pass runs
  after the writing transaction commits).
- A result with no job in flight, a mismatched (lang, key), a duplicate
  hello, or any malformed frame closes the socket with a protocol error.

Results are stored into `trans` in one transaction and set
`node.langs[lang]` on every article they touch (shared chunks make several
pages gain a language from one fragment). Unknown keys are stored anyway
and re-storing overwrites — results are idempotent.

#### Segmentation

Fragments cross the wire as **prose segments** (`pagerite/segments.py`): the
fragment is parsed with the project's own markdown-it setup
(`markdown.make_md(verbatim=True)` — all extensions, but no typographer or
tasklist label wrapping, so token text stays byte-identical to the source)
and split into the runs a model may touch: paragraph/heading/table-cell text
(merged across soft line breaks), image alt texts and captions, footnote
bodies. A block of plain text, inline **links and paired text formatting**
(strong/em/s) **stays whole** — link and formatted texts cross inline, in
sentence context, with the Markdown stripped (see below). Everything else
never leaves the server: code spans and
fences, URLs and autolinks, link/image *destinations*, `{...}` spans
(placeholders like `{dates}` as well as attrs), reference and footnote
labels, container fences, GFM alert markers (`[!NOTE]`), raw HTML — and the
remaining markup punctuation (`|`, `:::`), which is a run boundary.
Chunks with no segments (a lone `{dates}`, container fences, pure
code/HTML) are never dispatched at all (`needs_translation`); every
language renders them from the original chunk. Each segment is accompanied
by a context string (a segment carved out of a larger block carries the
block's plain text; a whole-block segment carries "") — context is a
prompt aid only, never spliced into the result.

Reassembly is offset splicing, not text the model produced: each segment's
source span was located at dispatch (sequential search; a run that is not a
verbatim source substring — entity-decoded text, backslash escapes — is
skipped and stays in the original language), and the returned translations
are swapped in by offset. Markup corruption is therefore impossible by
construction; the failure modes that remain are a wrong segment count, an
empty segment, or markup injected INTO a segment (a `<br>` in a title
translation would splice live HTML) — each returned segment must parse as
pure prose, or the whole result is dropped and logged, and the (lang, key)
pair is skipped for the rest of the server run (generation is
near-deterministic, so an immediate retry would re-fail; the fragment stays
pending and gets another chance on restart or `DELETE /_api/translations`).
`Data.trans` therefore only ever holds clean translated Markdown.

Link- and formatting-carrying blocks are the one place a segment is not
spliced verbatim: a label translated apart from its sentence comes back
grammatically incompatible with it (case government, particles, word
order), and shown the Markdown the model mangles it (Seed-X dropped the
`**` and the glued-on colon in `**Pagerite**: …`), so the block crosses
whole — all Markdown stripped — and the server re-inserts the link and
formatting syntax into the translated block. The boundaries are found by
**text processing alone** —
markers on the wire are hopeless (an earlier sentinel-masking design let
the model see and mangle exactly that punctuation: Seed-X renumbered the
tokens and turned `![` into `¡¡…!!`). Each mark's source words are aligned
to the translation's words by **form similarity** (`_find_mark` in
segments.py): a word-level alignment (sequence ratio plus shared prefix,
case-folded — inflection moves word endings, `banana` → `banaanilla`, and
articles or prepositions drop out; a mid-sentence capital on BOTH sides
earns a bonus, naming conventions being the likeliest shared cause) with
small penalties for skipped words, so reordering and dropped function
words don't break the match. An alignment is accepted only with an anchor
(one pair of similarity ≥ 0.7) and a decent average, and the slice is cut
exactly at word boundaries, so the whitespace between the mark and its
neighbors stays in the plain text. A mark with no convincing alignment
falls back to its weight ratio in the source block (word units before its
text boundaries over the block total) applied to the translation's units
— CJK ideographs count as one unit each, kana runs as one; for CJK targets
the fallback IS the path, cross-script form similarity being nil. Placement is
approximate and several reordered marks in one block can still cluster —
the accepted trade: better a coherent sentence with a slightly shifted link
than separately translated snippets that don't fit together. A boundary
that maps to an empty slice degrades to the source text rather than
emitting a broken `[](url)` or `**`. Blocks mixing in any other inline
markup (code spans, images, raw HTML) don't qualify and still split into
runs at those boundaries.

Punctuation is the translator's own job: Seed-X tends to "finish" short
labels (titles, nav items) with a comma or period the source never had.
Prompt wording is NOT the fix — a punctuation-instruction clause made
Seed-X slip into its `[COT]` reasoning mode (minutes-long generations with
reasoning text in the output, observed for Chinese). The reference client
enforces punctuation deterministically instead (`match_punctuation` in
scripts/translator.py): a translation of a segment without terminal
punctuation gets any added trailing marks (and a newly opened Spanish ¡/¿)
stripped before the result goes back.

The same client-side enforcement covers markup bleed as a CLASS, not per
artifact: `<` is the prose/markup boundary on the wire and never appears in
a segment in either direction. A literal `<` in the source text (`<1MB` is
text, not markup — a tag needs a letter or `/!?`) crosses encoded as the
fullwidth `＜` and is decoded on return, before the result is validated and
spliced (segments.py) — the wire itself still never carries `<`, and the
reference client cuts the model's output at the first `<`
(scripts/translator.py) — echoed language tags, stray `<br>`s and any
future variant are one handled case. (The cut is post-decode, not a
generation stop string: Seed-X opens every generation with its `<s>`
framing token, which would trip a `<` stop immediately.)

Server-side, a second layer covers what the inline parser cannot: ASCII
punctuation that is plain prose on the wire but Markdown syntax in the
splice context — quotes (a translated `"` would close the quoted image
title it lands in), brackets (alt texts, re-inserted link texts), `|` in
table rows, `\` escapes. Rather than rejecting such results, `join` swaps
them for Unicode look-alikes before splicing (`_NEUTRAL` in
segments.py — curly quotes, fullwidth brackets; the renderer's
typographer curls straight quotes anyway).

Short fragments get more than a bare prompt: each segment may carry its
surround in `Job.contexts` — a title carries the article's opening prose
(its own block is just the title word), a segment carved out of a larger
block (a partial run; a link text whose block didn't qualify for the
whole-block treatment) carries the block's plain text, and a
whole-block segment (a plain paragraph) is self-contextualizing and carries
"". The reference client translates segment and surround together, stops
generation at the blank line separating them, and keeps the segment's own
part of the output (its line resp. paragraph; a hard-break `␣␣\n` separator
works too). If the model merged them (no separator, or an empty first
part), it falls back to translating the segment alone. The surround fixes
context-free readings ("About" as "approximately" — with the opening it
becomes "Tietoa"/"Acerca de"; "here" as "就在这里" → the idiomatic
"点击这里") and, as a side effect, most stray trailing punctuation.

### Explicitly out of scope for phase 2

- The machine translation itself: the API above moves fragments in and out;
  the translating is external. `scripts/translator.py` is the reference
  client (Seed-X-PPO-7B only — its 28 languages are the ceiling).
- Garbage collection of orphaned chunks/translations (see docs/migrate.md).
- sitemap.xml per-language entries; translated UI chrome; per-language
  typographer options; multi-locale date/number formatting.
