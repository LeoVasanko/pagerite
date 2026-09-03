# migrate_v3: content-addressed chunk storage

Status: **implemented**. `migrate_v3` restructures how article text and
translations are stored, motivated by the localization model in
`docs/localization.md` (phase 2). Since it is a full migration, it is free to
break the current `Node.content: str | None` layout.

## Goals

- **Minimal change diffs.** kanta persists change diffs; editing one
  paragraph of a long article must not rewrite the whole article string, and
  a translation refresh must touch only the re-translated chunks.
- **Fast, simple lookup.** Everything heavy lives in flat
  `dict[hash, content]` stores; ordering lives in `list[hash]`. No large
  nested structures, no deep paths.
- **Path-independent text.** Chunks and their translations are keyed by
  content hash, not by article path — the same paragraph (or menu title)
  appearing in several articles is stored and translated once. Moving or
  renaming an article touches nothing.

## Design (chosen: global content-addressed stores)

Original articles are *also* stored as chunks; everything — originals and
translations — lives in flat hash-keyed dicts. Costs accepted: rendering does
one dict lookup per chunk (trivial), orphaned hashes need occasional garbage
collection, and the editor save path re-chunks server-side (it already
diffs). The rejected alternatives: per-article nested `LangVersion`
structures (churn, duplication, whole-string originals) and a hybrid with
whole originals plus global translations (keeps the worst change-diff
property).

## Target layout

```python
class Node(msgspec.Struct, omit_defaults=True):
    ...
    #: Replaces `content: str | None`. None = pure category label;
    #: a list (possibly empty) = a page, as ordered chunk hashes.
    chunks: list[bytes] | None = None
    #: Primary language of the article (BCP-47 base tag). "" = inherit
    #: (nearest ancestor, front page last, site default "en" final).
    language: str = ""
    #: Chunk hashes the editor marked "do not translate" (always served
    #: from the original). Presence-keys, value always True.
    no_trans: dict[bytes, True] = {}
    #: Languages this article is available in (besides its primary
    #: language). Presence-keys, value always True — rendering, language
    #: selection and hreflang alternates read this set instead of probing
    #: the trans store chunk by chunk. Maintained by the writers (see
    #: "Language index maintenance" below).
    langs: dict[str, True] = {}

class Data(msgspec.Struct):
    ...
    #: API keys gating the translator service WebSocket (/_translate/{key}):
    #: key -> display name; the first is generated at bootstrap (app.py).
    translate_keys: dict[str, str] = {}
    #: Wanted target languages for the translator service (presence-keys);
    #: jobs are offered only in these ∩ a connection's capabilities.
    translate_langs: dict[str, True] = {}
    #: All original-language text, content-addressed: blake3(normalized)
    #: digest[:9] -> Markdown chunk. Shared by every article. Keys are
    #: bytes; kanta/msgspec base64-encode them at the JSON level.
    chunks: dict[bytes, str] = {}
    #: Machine translations: chunk hash -> lang -> translated Markdown
    #: (nested, not tuple keys: msgspec's JSON serializer rejects them).
    #: Also used for node titles (hash of the title text).
    trans: dict[bytes, dict[str, str]] = {}
    #: User override patches per article and language:
    #: f"{path}:{lang}" -> ordered patches (see localization.md).
    patches: dict[str, list[Patch]] = {}
```

Notes:

- **Article paths never carry a leading slash** in the DB or in lookup keys
  (`"docs/setup"`, front page `""`); the leading slash is added only when
  building hrefs. `migrate_v3` audits existing stored paths (translation
  keys, analytics references, any path-valued fields) and normalizes them.
- **Titles are chunks too**, by hash only: the nav renderer looks up
  `trans.get(hash(node.title), {}).get(lang)`. No separate title storage;
  editing a title invalidates its translations automatically.
- **Per-hunk options** live in two places: *inherent* options are derived at
  chunking time (code fences, HTML blocks and prose-free chunks are
  no-translate without storing anything — `needs_translation`, see
  docs/localization.md "Masking"); *editor-set* flags are `node.no_trans`
  (keyed by chunk
  hash, so a heavy edit silently drops the flag — acceptable and
  self-healing).
- **Patch payloads stay inline** in `Patch.hunks` — patches are small by
  construction (minimal server-computed diffs). If a pathological case shows
  up, hunks can be hash-stored later without schema pain.

## Language index maintenance (`node.langs`)

`node.langs` is a denormalized index over the `trans`/`patches` stores so
that article rendering, `select_language`'s availability check, and hreflang
alternate links never enumerate chunks. It is written by whoever writes
translation data, in the same transaction:

- **Translator service:** the WebSocket API at `/_translate/{key}` (see
  docs/localization.md) offers pending fragments (titles + translatable
  chunks lacking an entry for the language) as single-item jobs — one at
  a time per connection, in `Data.translate_langs` ∩ the connection's
  announced capabilities — and receives the matching result; storing it
  writes the `trans[h][lang]` entry, sets `node.langs[lang] = True` on
  every article that gained one and invalidates the page cache — all in
  one transaction.
- **Translated-view save:** appending the first patch for `f"{path}:{lang}"`
  sets `node.langs[lang] = True` (patches alone make the version exist).
- **Removals:** deleting a patch or GC'ing translations re-derives the key:
  keep `lang` if any `trans` entry for the article's current chunks/title or
  any patch remains, otherwise drop it. Stale `langs` keys are benign (an
  advertised language that renders as the original), so removal can lag.

## Render / save pipeline (summary)

- **Render:** `text = "\n\n".join(chunks[h] for h in node.chunks)` for the
  original; for language `L` (only ever attempted when `L in node.langs`),
  per chunk `trans.get(h, {}).get(L)` unless missing or `h in node.no_trans`,
  falling back to `chunks[h]`; then apply `patches.get(f"{path}:{L}", [])`
  in order (per-hunk, best effort); then `markdown.render` as today. All of
  this assembles the `Translation` the phase-1 plumbing already consumes.
- **Availability:** `node.langs` is the availability index; `?lang=`
  handling uses exactly this set. (hreflang alternates are site-wide from
  `translate_langs` instead — see docs/localization.md.)
- **Save (primary language):** server re-chunks the submitted Markdown,
  inserts new hashes into `Data.chunks`, replaces `node.chunks`. Unchanged
  chunks keep their hashes — only genuinely new text lands in the diff.
- **Save (translated view):** diff against the served hybrid, append a
  `Patch` under `patches[f"{path}:{lang}"]`; `node.chunks` untouched.
- **Invalidate:** any write to `chunks` / `trans` / `patches` calls
  `_invalidate_pages()`.

## migrate_v3 steps

1. Walk `menu`; for every node with a string `content`:
   `chunks = chunk_markdown(content)`; write each into the new `chunks`
  store; replace the field with the hash list (`None` stays `None`).
2. Initialize empty `chunks` / `trans` / `patches` stores.
3. Normalize stored paths: strip leading slashes anywhere paths are keys or
   values.
4. `language`, `no_trans` and `langs` need nothing — struct defaults cover
   them (`langs` starts empty; the translator job fills it as translations
   land).

Chunking must be deterministic and shared with render/save, so
`chunk_markdown` + `chunk_key` live in `pagerite/i18n.py` (or a small
`pagerite/chunks.py`) and are imported by both `migrations.py` and
`views.py`/`app.py`.

## Implementation notes (deviations from the plan above)

- Chunking lives in `pagerite/chunks.py`; hashing uses the `blake3` package
  (already a dependency), truncated to a 9-byte `bytes` digest (kanta's
  JSON persistence base64-encodes bytes keys to 12-char strings).
- `trans` is keyed `hash -> lang -> text` (nested dict), not by
  `f"{hash}:{lang}"` tuples: msgspec's JSON serializer only supports
  str-like/number-like dict keys, and kanta persists as JSON lines.
- `Translation.titles` stayed keyed by node path (phase-1 shape, views
  untouched): `get_translation` builds it by walking the menu with the same
  per-title `trans.get(chunk_key(node.title), {}).get(lang)` lookups.
- Insert hunks anchor on the whole preceding block (not just its tail) —
  a stronger, simpler search context.
- `make_patch` diffs with `SequenceMatcher(autojunk=False)` so patches are
  deterministic (popular lines like blank separators never become junk).
- Step 3's path normalization is a no-op in practice: the only path-keyed
  store (`patches`) starts empty at v3; analytics paths live outside the
  kantadb. The code still strips leading slashes defensively.

## Garbage collection (later, manual or idle-time)

Orphaned entries accumulate: chunks no longer referenced by any
`node.chunks`/`node.title`, translations whose chunk hash is orphaned, patch
hunks that never match. All are harmless (never read). A GC pass is a single
tree walk collecting live hashes, then deleting the rest from `chunks` and
`trans`; patches whose every hunk is stale get pruned. Not part of
migrate_v3.
