# AGENTS.md

- Do NOT test, run the server, write tests etc.
- ESPECIALLY DO NOT make repros, do NOT install Playwright etc.

Please instead ask the user to see from dev tools what you need, e.g. to look up something in DOM or log. Use console.log for debugging where needed (and otherwise for permanently kept useful messages in the app).

## Layout

Pagerite is a CMS. See `docs` for the full design and implementation details. Key files for code changes:

- `pagerite/` — Python backend package (hatchling build target).
  - `app.py` — FastAPI app and route registration.
  - `data.py` — msgspec Structs for the kanta database.
  - `markdown.py` — markdown-it-py renderer.
  - `views.py` — shared page layout and rendering.
  - `seed.py` — demo content, written only on first database creation.
  - `analytics.py` — visit analytics collection (see `docs/analytics.md`).
- `frontend/src/` — Vue editor and public-page JS entries.
  - `main.js` — Vue editor app entry.
  - `analytics-main.js` — analytics page entry (mounts `AnalyticsView` at `/_a`).
  - `pagerite.js` — public page entry.
  - `assets/` — base CSS, Pygments styles, fonts.
- `scripts/devserver.py` — dev server with auto reload (the user mostly uses this; avoid running the server yourself, ask the user to test).

Server run by CLI entry point `uv run pagerite` (no auto reloads, build needed). Dev mode is `scripts/devserver.py` (auto reloads, no build needed).

## Toolchain

- Python >= 3.14, managed with **uv**. Dependencies: `fastapi[standard]`, `fastapi-vue`, `html5tagger`, `kanta`, `markdown-it-py`, `mdit-py-plugins`, `pygments`, `tracerite`; dev group has `httpx`. Run anything via `uv run ...` (the venv is `.venv`).
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
  - **markdown-it-py** — Markdown rendering with `html=True` raw passthrough; mdit-py-plugins for footnote/deflist/tasklists/attrs; **Pygments** for server-side code highlighting (`nowrap` spans, styled by `frontend/src/assets/pygments.css` which maps token classes 1:1 onto the `--code-*` variables; light/dark palette sets live in `pagerite.css` and resolve via `light-dark()` from the theme's `color-scheme` — themes pick a set, not individual colors).

## Conventions

- Keep dependencies minimal; add via `uv add` and mention it.
- The public URL space belongs to content (pretty slugs at root). Reserve only `/_` for the machinery (`/_api/`, `/_f/`, `/_assets/`), plus `/favicon.ico` from the build. Slugs are lowercase ASCII letters, digits, hyphens and underscores `[a-z0-9_-]` (the site editor filters input live via `slugify.js`, built on the `transliteration` npm package — unicode folds to ASCII, spaces become hyphens; an empty slug on a new page is derived from its title), may not begin with `_` or `.`, and such URLs are never looked up as content.
- No auth in core code; the SSO/reverse proxy gates all of `/_api` (forward-auth) and owns `/auth/` (login/logout, session validation). Pages render identically for everyone; pagerite.js adds the editing UI only after the auth server validates the session.
- Update the relevant MarkDown files when architecture, tooling, or conventions change.
