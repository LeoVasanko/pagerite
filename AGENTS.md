# AGENTS.md

- Do NOT test, run the server, write tests etc.
- ESPECIALLY DO NOT make repros, do NOT install Playwright etc.

Please instead ask the user to see from dev tools what you need, e.g. to look up something in DOM or log. Use console.log for debugging where needed (and otherwise for permanently kept useful messages in the app).

## What this is

Pagerite: a single-user CMS/blog. FastAPI serves HTML rendered in Python
with html5tagger; content is persisted in a kanta database and rendered on
the fly per request. Vue is used only for interactive bits (editing tools),
not for the public pages. See `docs/design-principles.md` for the design.

## Layout

- `pagerite/` — the Python backend package (hatchling build target).
  - Server run by CLI entry point `uv run pagerite` (no auto reloads, build needed)
  - Dev mode `scripts/devserver.py` (which the user mostly uses for auto reloads, no build needed)
  - Avoid running the server yourself, ask the user to test
  - `app.py` — the FastAPI app. FastAPI's built-in API docs are disabled
    (`docs_url`/`redoc_url`/`openapi_url=None`) because `/docs` belongs to
    our content. Our own routes (content pages, `/_/api/...`, `/_/f/...`,
    `/_/admin`) are registered BEFORE `frontend.route(app, "/")` is
    called: fastapi-vue inserts its file routes at the position where
    `route()` was called (during `load()` in the lifespan), so anything
    defined earlier wins. The one exception is the content catch-all
    `/{path:path}`, registered AFTER `frontend.route()` so that built
    frontend assets still take priority over content slugs. The `Frontend`
    is constructed with `spa=False` explicitly: it only serves the built
    files without a catch-all. The build mirrors the URL space — hashed
    immutable assets under `/_/assets/`, `favicon.ico` at the site root —
    and an `index.html` in the build would become a `/` route, so leave it
    out of the build to keep `/` ours.
  - `data.py` — msgspec Structs for the kanta database. The site structure
    is a tree: `Data.menu` maps top-level slugs to `Node`s, each with
    `children` keyed by slug — the URL path is the slug chain. The front
    page is whichever top-level node has slug "" (parallel to the other
    main level pages, not their parent); it cannot have children, and
    renaming its slug away leaves no front page ("/" redirects to the
    first nav item). `Node.content` is
    the Markdown page, or None for a pure category label whose URL
    redirects to its first child; every label's title and slug are
    editable. Siblings order by the fractional `Node.order` key: a moved
    item gets a fresh key relative to its new siblings, all others keep
    theirs. `resolve`/`find_slot` walk the tree by path; moves are slot
    detach/attach carrying the whole subtree. Legacy flat `Data.pages`
    (pre-tree databases) migrates into `menu` on startup. The app owns
    the `Data` object; reads are plain attribute access, writes in
    `kanta.transaction(...)`.
    `Data.files` is a content-addressed store (blake3[:12] + extension)
    mapping file names to bytes, served at `/_/f/{name}` with immutable
    caching; pages reference files by absolute `/_/f/` URLs so hierarchy
    moves never break them. `Node.banner` is a raw trusted HTML snippet
    for the header banner (img, styled div, canvas+script...); empty
    inherits from the node's ancestors (front page last), then the default
    banner.svg artwork. `Data.version` is bumped on every write
    and embedded in page ETags so nav-affecting changes invalidate caches.
    `Data.brand` is the site name (header link + `<title>` suffix), editable
    in the site editor via `/_/api/settings`; empty = no header link and
    no `<title>` suffix.
  - `markdown.py` — markdown-it-py renderer (html passthrough + attrs,
    footnote, deflist, tasklists plugins). Custom image rule: relative srcs
    resolve against the page path, titled images become figures.
  - `views.py` — the shared page layout as an html5tagger `Template` with
    placeholders (`Title`, `Brand`, `Banner`, `Nav`, `Sidebar`, `Main`), nav
    rendering straight from the `Data.menu` tree (siblings sorted by
    `Node.order`; content-less labels redirect to their first child via
    `first_leaf`), and page/404 rendering. If the markdown contains its own h1, the page title
    is NOT rendered as an additional h1 (it still supplies <title> and nav
    labels). The navbar holds
    top-level items only; the current section's subitems go to a left
    `#sidebar` (empty and hidden elsewhere). Dynamic regions have stable ids
    (`#page-banner`, `#nav`, `#sidebar`, `#main`) for fetch-navigation swaps.
  - `seed.py` — demo content written on startup for paths missing from the
    database (never overwrites existing pages).
  - `frontend/src/` — the Vue editor and public-page entries.
    - `main.js` — Vue editor app entry, mounts PageEditor/SiteEditor.
    - `pagerite.js` — public page entry; imports the shared style and runs
      fetch-navigation, scroll-reveal and code copy buttons.
    - `assets/` — shared styles and data files built by Vite and served hashed
      under `/_/assets/`: `style.css`, `pygments.css`, `banner.svg` and
      `fonts/` (self-hosted Fraunces/Literata/Fira Code variable woff2). The
      `::view-transition*` block at the end of `style.css` (from
      termotohtori.fi) is fragile — do not tweak.
    - Vite builds ES-module `.js` outputs; the backend renders `<script
      type="module" defer>` for them.
  - The database file is `pagerite.kanta` in the cwd (`PAGERITE_DB`
    overrides); gitignored. Do not delete it without asking.
- `scripts/fastapi-vue/` — helper scripts from the fastapi-vue template
  (build hook etc.), do not edit.
- `frontend/` — the Vue editor as **two separate apps** mounted in their
  own host divs created inside the static document: `PageEditor.vue`
  (CodeMirror + server-rendered preview over WebSocket `/_/api/ws/editor`,
  previewing into the visible article; editor scroll drives document
  scroll) opened by the article pen — it edits content and title only,
  never the path — and `SiteEditor.vue` (site brand + banner HTML edited in
  a small CodeMirror window and previewed into `#page-banner` + vue-draggable structure tree with
  always-editable title/slug inputs per row) opened by
  the banner pen — everything saves immediately as you edit (brand/title
  debounced, slug on commit since it renames the path), tree rows navigate
  in place without transitions when focused, and the front page is a
  root-only row whose empty slug is editable like any other (empty child
  lists become drop zones while dragging). The two pens swap the docked
  panel for the other editor; clicking the open editor's own pen closes it. Normally dynamic-imported onto the content page by
  pagerite.js when a 🖊️ edit link is clicked (the link carries
  `data-editor-src`/`data-editor-css`/`data-editor-mode`); the `/_/admin`
  route (page selected by location hash) is the no-JS-import fallback shell
  rendered by `views.render_editor` and keeps its own preview pane.
  In dev, modules load from the Vite dev server (`PAGERITE_VITE_URL`),
  in prod from the hashed build assets resolved via
  `frontend-build/.vite/manifest.json`. `vite.config.js` builds with
  `manifest: true`, `assetsDir: '_/assets'` (so the build mirrors the URL
  space; `frontend/public/favicon.ico` lands at the build root and is
  served at `/favicon.ico`) and JS inputs (`src/main.js` and
  `src/pagerite.js`) so no `index.html` ends up in the build (it would shadow
  `/`). All outputs are ES modules. vite-plugin-fastapi.js has an
  auto-upgrade marker — edit `vite.config.js`, not the plugin.
- `docs/` — design documentation.

## Toolchain

- Python >= 3.14, managed with **uv**. Dependencies: `fastapi[standard]`,
  `fastapi-vue`, `html5tagger`, `kanta`, `markdown-it-py`, `mdit-py-plugins`,
  `pygments`, `tracerite`; dev group has `httpx`. Run anything via
  `uv run ...` (the venv is `.venv`).
- Key libraries:
  - **html5tagger** — all HTML generation (`E`, `Document`, `Template`,
    `HTML` for trusted/raw HTML).
    - To create stand alone pages, begin with `doc = Document(...)` that gives a HTML5 page header
    - Chain with `doc.p("text").br`: every attribute access creates element to doc (returning self), calls add content to current element.
    - Closing tags are not used where optional, e.g. no `</p>` or `</li>` is ever included in output. Due to this proper "nesting" of content is NOT required and should be avoided. Where needed, () directly after tag define attributes and content INSIDE the element, then close the element. `with doc.ul:` and such may be used for larger chunks.
    - Prefer building directly on one builder with `with` blocks (recursing
      inside a with block for hierarchies) over preparing `E.` snippets into
      variables and composing them. Note `with doc.li:` alone fails (`li`
      has an optional end tag) — use `with doc.li.ul:` style chains, or
      `doc.li.a(...)` followed by a nested `with doc.ul:` block.
    - `Template(builder)` freezes a builder with **Capitalized** attribute
      placeholders (e.g. `E.Title`, `doc.main(E.Main, id="main")`); calling
      it fills the slots with escaping — pass `HTML(...)` for raw HTML.
      Passing a list to a template slot expands it; passing a list to a
      normal builder call does NOT (spread it: `E.ul(*items)`).
    - To create plain HTML snippets use `E.div(E.p("content"))` etc using the `E` empty builder.
  - **kanta** — asyncio-native embedded database: `Kanta(filename, data)`
    root object, `transaction`, `flush`, snapshot/replay-log persistence.
    - `async with Kanta(Data(),...) as kanta:` (or await kanta.open/close)
    - `with kanta.transaction(...) as data:` - transactions only for writes
    - `data` may be referenced directly to read anywhere and to modify in transactions (`as data` is just a shorthand access)
    - Data structures should be msgspec.Structs, where JSON restrictions do not apply (we can use `bytes`, `UUID`, `datetime` etc. even as dict keys)
      - We prefer objects rather than lists, as this works better in change diffs. E.g. `dict[str, True]` where the keys indicate presence and always have value `True`.
    - Maintaining and owning the app's own `Data` object is preferable; Kanta never copies this, only edits in place
    - Note: besides opening it every access is immediate direct variable access: no `await`, no locks, no delays
  - **fastapi-vue** — template glue for serving/building the Vue frontend;
    keep its integration points (`Frontend`, build hook) intact.
  - **markdown-it-py** — Markdown rendering with `html=True` raw
    passthrough; mdit-py-plugins for footnote/deflist/tasklists/attrs;
    **Pygments** for server-side code highlighting (`nowrap` spans, styles
    in `frontend/src/assets/pygments.css` scoped to "pre code").

## Conventions

- Keep dependencies minimal; add via `uv add` and mention it.
- The public URL space belongs to content (pretty slugs at root). Reserve
  only `/_/` for the machinery (files, API, built assets, admin), plus
  `/favicon.ico` from the build. Slugs are lowercase ASCII `[a-z0-9-]`
  (the site editor filters input live via `slugify.js`, built on the
  `transliteration` npm package — unicode folds to ASCII, spaces become
  hyphens; an empty slug on a new page is derived from its title), may
  not begin with `_` or `.`, and may not be a reserved file name
  (`robots.txt`, `ads.txt`, `sitemap.xml`, `openapi.json`, `favicon.ico`,
  `site.webmanifest`). The API rejects such paths with a human-readable
  reason shown in the editor, and such URLs are never looked up as
  content when serving.
- No auth in core code; trusted single author. Never add output
  sanitization "for safety" against the author — embedded HTML/scripts in
  Markdown are passed through deliberately.
- Update this file and `docs/design-principles.md` when architecture,
  tooling, or conventions change.
