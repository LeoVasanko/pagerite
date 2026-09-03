# AGENTS.md

- Do NOT test, run the server, write tests etc.
- ESPECIALLY DO NOT make repros, do NOT install Playwright etc.

Please instead ask the user to see from dev tools what you need, e.g. to look up something in DOM or log. Use console.log for debugging where needed (and otherwise for permanently kept useful messages in the app).

## Layout

Pagerite is a CMS. See `docs` for the full design and implementation details. Key files for code changes:

- `pagerite/` — Python backend package (hatchling build target).
  - `app.py` — FastAPI app and route registration.
  - `data.py` — msgspec Structs for the kanta database.
  - `chunks.py` — block-level Markdown chunking and content-hash keys for the chunk stores (docs/migrate.md).
  - `i18n.py` — language selection, translation assembly (chunks + patches) and translated-edit recording (user patches, per-language title overrides, refresh).
  - `translate.py` — translator service protocol (msgspec structs), the connected-client `Dispatcher` (job pipeline, result validation) and pending/store core for the `/_translate/{key}` WebSocket (docs/localization.md); app.py only registers the route.
  - `segments.py` — the translation round trip: fragments split into pure-prose wire segments (via markdown.make_md's verbatim parser; link- and formatting-carrying blocks stay whole, link/formatted texts inline, Markdown stripped) and translations spliced back by source offset, link/formatting markdown re-inserted at weight-mapped positions (docs/localization.md).
  - `migrations.py` — kanta migrations (`migrate_vN`); ALL schema/storage upgrades live here (raw state dict before struct decoding), never in the app lifespan: v1 moves legacy in-db file blobs to the on-disk store and rebuilds the legacy flat `pages` as the menu tree, v2 rewrites `/_f/{hash}.ext` image links to the extension-less form, backfills AVIF/WebP/JPEG derivatives on disk and drops the obsolete `version` field.
  - `markdown.py` — markdown-it-py renderer.
  - `views.py` — shared page layout and rendering; theme/user-font resolution across `THEME_DIRS` / `FONT_DIRS` (cwd, site, platform data roots, then built-in `pagerite/themes/`, see `docs/themes-and-assets.md`).
  - `seed.py` — demo content, written only on first database creation.
  - `analytics.py` — visit analytics collection (see `docs/analytics.md`).
- `frontend/src/` — Vue editor and public-page JS entries.
  - `main.js` — Vue editor app entry.
  - `analytics-main.js` — analytics page entry (mounts `AnalyticsView` at `/_a`).
  - `pagerite.js` — public page entry.
  - `editorLang.js` + `LangSelect.vue` — the editor shell's shared language selection and its selector component (page + structure tabs; drives the page preview while the panel is open, via `swapdoc.setLangOverride`).
  - `reconnect.js` — shared WebSocket pacing for all sockets (staggered connect slots, stuck-CONNECTING watchdog, exponential backoff): bursts and rapid retries trip the browser's WebSocket throttling.
  - `assets/` — base CSS, Pygments styles, fonts.
- `scripts/devserver.py` — dev server with auto reload (the user mostly uses this; avoid running the server yourself, ask the user to test).
- `scripts/translator.py` — Seed-X translator service client for the `/_translate/{key}` socket (reference client, runs in its own uv env via PEP 723); stays connected full time, unloads the model after 60 s idle and reloads on the next job.

Server run by CLI entry point `uv run pagerite` (no auto reloads, build needed). Dev mode is `scripts/devserver.py` (auto reloads, no build needed).

## Toolchain

- Python >= 3.14, managed with **uv**. Dependencies: `fastapi[standard]`, `fastapi-vue`, `html5tagger`, `kanta`, `markdown-it-py`, `mdit-py-plugins`, `platformdirs`, `pygments`, `tracerite`; dev group has `httpx`. Run anything via `uv run ...` (the venv is `.venv`).
- Key libraries:
  - **html5tagger** — all HTML generation (`E`, `Document`, `Template`, `HTML` for trusted/raw HTML).
    - To create stand alone pages, begin with `doc = Document(...)` that gives a HTML5 page header
    - Chain with `doc.p("text").br`: every attribute access creates element to doc (returning self), calls add content to current element.
    - Closing tags are not used where optional, e.g. no `</p>` or `</li>` is ever included in output. Due to this proper "nesting" of content is NOT required and should be avoided. Where needed, () directly after tag define attributes and content INSIDE the element, then close the element. `with doc.ul:` and such may be used for larger chunks.
    - Prefer building directly on one builder with `with` blocks (recursing inside a with block for hierarchies) over preparing `E.` snippets into variables and composing them. Note `with doc.li:` alone fails (`li` has an optional end tag) — use `with doc.li.ul:` style chains, or `doc.li.a(...)` followed by a nested `with doc.ul:` block.
    - `Template(builder)` freezes a builder with **Capitalized** attribute placeholders (e.g. `E.Title`, `doc.main(E.Main, id="main")`); calling it fills the slots with escaping — pass `HTML(...)` for raw HTML. Passing a list to a template slot expands it; passing a list to a normal builder call does NOT (spread it: `E.ul(*items)`).
    - To create plain HTML snippets use `E.div(E.p("content"))` etc using the `E` empty builder.
  - **kanta** — asyncio-native embedded database: `Kanta(filename, data)` root object, `transaction`, `flush`, snapshot/replay-log persistence.
    - `async with Kanta(Data(),...) as kanta:` (or await kanta.open/close)
    - `with kanta.transaction(...) as data:` - transactions only for writes
    - `data` may be referenced directly to read anywhere and to modify in transactions (`as data` is just a shorthand access)
    - Data structures should be msgspec.Structs, where JSON restrictions do not apply (we can use `bytes`, `UUID`, `datetime` etc. even as dict keys)
      - We prefer objects rather than lists, as this works better in change diffs. E.g. `dict[str, True]` where the keys indicate presence and always have value `True`.
    - Maintaining and owning the app's own `Data` object is preferable; Kanta never copies this, only edits in place
    - Note: besides opening it every access is immediate direct variable access: no `await`, no locks, no delays
  - **fastapi-vue** — template glue for serving/building the Vue frontend; keep its integration points (`Frontend`, build hook) intact.
  - **platformdirs** — platform user/system data dirs for the theme and font search roots (`views.THEME_DIRS` / `views.FONT_DIRS`; use `site_data_dir(..., multipath=True)`, not `site_data_path`, which collapses multipath).
  - **markdown-it-py** — Markdown rendering with `html=True` raw passthrough; mdit-py-plugins for footnote/deflist/tasklists/attrs; in-body h1/h2 headings get auto slug ids + self-links when the body has 3+ of them (`python-slugify`, mirroring `slugify.js`); **Pygments** for server-side code highlighting (`nowrap` spans, styled by `frontend/src/assets/pygments.css` which maps token classes 1:1 onto the `--code-*` variables; light/dark palette sets live in `pagerite.css` and resolve via `light-dark()` from the theme's `color-scheme` — themes pick a set, not individual colors).

## Conventions

- Keep dependencies minimal; add via `uv add` and mention it.
- The public URL space belongs to content (pretty slugs at root). Reserve only `/_` for the machinery (`/_api/`, `/_f/`, `/_assets/`), plus `/favicon.ico` from the build. Slugs are lowercase ASCII letters, digits, hyphens and underscores `[a-z0-9_-]` (the site editor filters input live via `slugify.js`, built on the `transliteration` npm package — unicode folds to ASCII, spaces become hyphens; an empty slug on a new page is derived from its title), may not begin with `_` or `.`, and such URLs are never looked up as content.
- No auth in core code; the SSO/reverse proxy gates all of `/_api` (forward-auth) and owns `/auth/` (login/logout, session validation). Pages render identically for everyone; pagerite.js adds the editing UI only after the auth server validates the session. The one keyed exception is `/_translate/{key}` (translator service; `Data.translate_keys`, see docs/localization.md).
- Update the relevant MarkDown files when architecture, tooling, or conventions change.
