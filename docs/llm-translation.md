# Whole-article and scoped LLM translation

**Status: implemented** (protocol modes and validation in
`pagerite/translate.py`, reference client `scripts/llm_translator.py`,
human-translation import `scripts/import_translation.py`; wire-level docs
in docs/localization.md). One deviation from the text below: ollama's
OpenAI-compatible `/v1/chat/completions` silently ignores `think: false`
(verified on 0.34.2), so the client's `api` config selects ollama's native
`/api/chat` for ollama backends; the OpenAI shape serves llama.cpp and
hosted APIs.

Design for augmenting the fragment-based machine translation
(docs/localization.md) with general-purpose instruct LLMs that understand
Markdown natively — as opposed to pure text-to-text models like Seed-X.

## Motivation

The chunk + segment pipeline (`chunks.py` → `segments.py` → Seed-X) exists
because Seed-X mangles Markdown: links, formatting, fences and placeholders
must be stripped before dispatch and re-inserted into the result. The
re-insertion of link and formatting markup is the imprecise part: when
word-alignment by form similarity finds no anchor (always for CJK targets),
positions fall back to word-weight ratios, which land a word or so off.
All of `segments.py` — segmentation, offset splicing, `_find_mark`,
weight-ratio fallback, `_NEUTRAL` punctuation swaps, `<` encoding — is
defensive scaffolding around that one limitation.

An instruct LLM translates Markdown natively: `[text](url)` stays intact
and moves as a unit, fences, container markers, attrs and `{...}`
placeholders are preserved, and link texts translate in sentence context.
For such a translator the entire segments layer is unnecessary.

## Trial evidence (2026-09, RTX 4090 24 GB + 128 GB RAM, ollama 0.34)

Whole-article translation of two real articles (5.5 KB marketing, 18.3 KB
technical with code fences and `{dates}`) into fi/es/zh:

- **qwen3.8:27b** (dense, 17 GB Q4 — fits VRAM): structure-perfect on all
  runs — URLs, placeholders, heading/block counts preserved, fenced code
  byte-identical. es/zh excellent; fi fluent with occasional lexical slips
  (covered by the human patch layer). ~30 s per short article, ~2.5 min
  for 18 KB. **The reference model for article and markdown modes.**
- **qwen3:30b-instruct**: 3× faster, good prose, but rewrote comments and
  docstrings inside code fences despite explicit instructions — fails
  anchor validation (see below).
- **qwen3-next:80b** (MoE): best Finnish word choice on short documents,
  but degenerates on longer input in every configuration tried — runaway
  thinking loops (293k tokens), empty responses, 3× length output with
  hallucinated URLs, and ~10× blowup even in 2 KB scoped chunks. Unusable
  on current ollama builds.
- **CPU-only** (i7-14700, 14 threads): MoE 3B-active 10.6 t/s generation
  (viable for batch), dense 27B 2.5 t/s (not viable). Hybrid GPU+CPU
  splits bottleneck prompt evaluation (~52 t/s vs 62 t/s pure CPU) —
  dense GPU-resident or MoE CPU-resident are the sane configurations;
  mixing hurts.

Operational requirements established by the trials (all client-side):

- **Always disable thinking** for hybrid models (`think: false` on
  ollama): the reasoning phase adds minutes per article and can loop
  unbounded.
- **Always cap generation** (`num_predict` ≈ 2–3× input tokens): a
  runaway on a whole-article job burns hours, vs. seconds for a
  Seed-X segment.
- temperature 0.2 with the strict structure prompt works well for
  qwen3.8.

## What carries over unchanged

The valuable parts of the current design are the **storage and staleness
model**, not the segmentation — and none of them require the machine
translation to be produced chunk by chunk. The chunk store is a
storage/diffing format; LLM output at any granularity is *projected
into* it:

- Content-addressed source chunks (`Data.chunks`, `chunk_key`) — staleness
  still falls out of source-hash keys: editing the original invalidates
  exactly the edited chunks, all other translations keep applying.
- Per-chunk machine translations (`Data.trans[hash][lang]`) and the hybrid
  render with per-chunk fallback to the original.
- User patches (`Data.patches`) — search/replace hunks over the assembled
  hybrid, per-hunk independent and best-effort. Patches are orthogonal to
  how `Data.trans` entries were produced.
- `pending_items`: after a source edit, exactly the changed (lang, hash)
  pairs are pending — **focused retranslation of edits falls out of the
  existing bookkeeping**, no whole-article reruns.

## Protocol: capabilities and job modes

The `/_translate/{key}` WebSocket stays the single channel; Seed-X
clients work unchanged. The client→server `Hello` gains two optional
fields:

```python
class Hello(msgspec.Struct, tag="hello"):
    langs: list[str]           # as today: languages the model can produce
    model: str = ""            # free-form model string (logging, debugging)
    modes: list[str] = ["segments"]  # job granularities accepted
```

Four job modes, in increasing granularity:

- **`segments`** — the current protocol, unchanged: `Job.texts` carries
  prose segments (markup never crosses the wire), `Result.texts` returns
  them, the server splices by offset (`segments.py`). For text-to-text
  models (Seed-X). Default when a client omits `modes`.
- **`markdown`** (scoped instruct mode) — one fragment as full Markdown:
  a body chunk or a title. `Job.texts` carries a single element, the
  chunk's Markdown; `Job.contexts` carries up to two context strings
  (previous and next block of the **served hybrid** in the target
  language — current machine translation with user patches applied),
  "" where none. The client is instructed to output ONLY the translation
  of the target block; the context is terminology/tone reference.
  Using the *patched* hybrid as context propagates human corrections
  into fresh machine translations without the LLM ever touching patch
  storage. `Result.texts` carries one element, the translated block.
  The server validates: exactly one block after re-chunking, anchor
  constructs (URLs, image destinations, code fence content, `{...}`
  placeholders) preserved where the source block has them, then stores
  to `Data.trans` as usual.
- **`article`** — a whole page. `Job.texts` carries one element, the full
  original Markdown (the chunk sequence is recoverable server-side via
  `node.chunks`); `Result.texts` carries one element, the full translated
  Markdown. The server decomposes (below) and stores per chunk.
- **`nav`** — the whole navigation hierarchy. `Job.texts` carries one
  element, a nested Markdown list of every node title still pending for
  the language (`- Title`, indented by depth, in menu order);
  `Result.texts` carries one element, the translated list. The server
  decomposes by list structure (`align_nav`): item count and nesting
  depth must match the source item for item, then each item is stored as
  a per-title fragment under its title's chunk hash.

Titles are jobs like any other in all modes (`kind="title"` keeps its
article-opening context rule; in `markdown` mode a title crosses as
plain text, since it carries no markup by construction) — but for
nav-capable connections a single `nav` job names the entire menu first:
one round trip instead of one per page, with siblings, parents and
children translating in sight of each other. A structurally mangled list
is rejected wholesale and the titles fall back to scoped title jobs.
Additionally, an
`article` job carries the page title injected as a `# {title}` line at
the top when the render would inject it (the body has no h1 of its own):
the title translates in document context and the opening paragraphs see
the heading. The menu title's and parent node's existing translations
ride along as `Job.contexts` ("" where none), so the heading can match
the menu while the model may still adapt the in-article title to the
content. The heading's pair in the decomposed result becomes the
title fragment (heading text only, never stored as a body chunk).

### Dispatch and validation

- Routing is per connection as today (wanted ∩ capable, one job in
  flight, requeue on disconnect), extended by mode: the smallest
  suitable unit goes to each free connection — `article` jobs only to
  article-capable connections, and only while a page is *mostly*
  pending (a whole new article or a full refresh); steady-state edit
  follow-up is `markdown`/`segments` jobs. Mixed translator fleets (a
  Seed-X instance, a local qwen, an API-backed client) run concurrently
  and share the work by capability.
- The validation skip-list becomes **mode-scoped** (`(lang, key, mode)`):
  a fragment a Seed-X client rejects stays offerable to instruct clients
  (and vice versa) — near-deterministic re-failure applies per model,
  not across approaches.
- `Result` matching is unchanged (lang, key); article results match on
  the key of the article's first chunk.

## Article result decomposition

1. Re-chunk the translated article with the same `chunk_markdown`.
2. Align translated blocks to source blocks. A well-behaved model does
   not reorder paragraphs, so positional / `SequenceMatcher` alignment
   at block granularity suffices. Blocks that must not change — code
   fences, container fence lines, `{...}` placeholders, image
   destinations, raw HTML — are matched verbatim and serve as alignment
   anchors, like diff context lines.
3. Store each translated block in `Data.trans[source_chunk_hash][lang]`.

Validation happens *before* anything is stored, same spirit as the
`pure_prose` segment checks but structural:

- Anchor blocks must appear verbatim and in order (this is what rejects
  qwen3:30b-instruct's translated code comments automatically).
- Per anchor-bounded region, source and translated block counts must
  match 1:1; regions that don't align store nothing and their chunks
  stay pending (they fall back to `markdown`-mode scoped jobs).

## Reference client

A second client script next to `scripts/translator.py` speaking the
`markdown` and `article` modes. Internally it targets the **OpenAI
Chat Completions API shape** (`POST /v1/chat/completions`): ollama
serves it at `:11434/v1`, llama.cpp's server likewise, and hosted APIs
(OpenAI and compatible providers) natively — `--base-url` + `--model`
selects local GPU, local CPU or a remote model, the API key comes from
the standard per-provider environment variable (`KIMI_API_KEY`,
`MOONSHOT_API_KEY`, `OPENAI_API_KEY`, each sent only to its own
provider's host; `LLM_API_KEY` for anything else) — deliberately never
a CLI flag or a config file — and backend quirks (ollama's
`think: false`, `num_predict` cap, per-model sampling) live in the
script's `DEFAULT_CONFIG`.
How the client drives its LLM is its internal matter; the wire protocol
above is the contract.

Field-proven backends: the local qwen3.8:27b of the trials above, and
the **Kimi Code API** (`--base-url https://api.kimi.com/coding` resp.
`api.kimi.ai`, `--model k3-256k`): the `/coding` endpoint fixes sampling
internally (the client drops `temperature`/`top_p` for it — they 400)
and runs `reasoning_effort: low` from the config, which produces good
translations at a fraction of the default (high) effort's latency and
quota; thinking output is logged verbatim but stripped from the result.

The client announces in `Hello`:

- `model`: the model string it is actually serving (e.g. `qwen3.8:27b`)
- `langs`: from its per-model language table — for the shipped qwen3.8
  configuration the site languages as configured server-side
  (de, es, fi, pt, zh; Finnish flagged as the weakest, patch-covered)
- `modes`: `["markdown", "article", "nav"]` for a structure-proven model,
  `["markdown"]` for one that is only trusted in scoped mode

The Seed-X client is untouched and announces `["segments"]` (implicitly,
by omitting `modes`).

## Importing human-made full translations

The decomposition function doubles as an import path for translations
produced outside the pipeline — e.g. an article translated with ChatGPT
and pasted back. Today such a paste lands in the translation editor and
is stored as one giant user patch; feeding it through the same
decomposition instead writes proper `Data.trans` fragments, so later
source edits invalidate and re-translate per chunk rather than letting
the monolithic patch silently go stale hunk by hunk. This import path is
also the natural testbed for the decomposition and validation logic
before any live LLM client uses it.
