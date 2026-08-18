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
    our content. Our own routes (content pages, `/_api/...`, `/_f/...`) are
    registered BEFORE `frontend.route(app, "/")` is called: fastapi-vue
    inserts its file routes at the position where
    `route()` was called (during `load()` in the lifespan), so anything
    defined earlier wins. The one exception is the content catch-all
    `/{path:path}`, registered AFTER `frontend.route()` so that built
    frontend assets still take priority over content slugs. The `Frontend`
    is constructed with `spa=False` explicitly: it only serves the built
    files without a catch-all. The build mirrors the URL space — hashed
    immutable assets under `/_assets/`, `favicon.ico` at the site root —
    and an `index.html` in the build would become a `/` route, so leave it
    out of the build to keep `/` ours.
  - `data.py` — msgspec Structs for the kanta database. The site structure
    is a tree: `Data.menu` maps top-level slugs to `Node`s, each with
    `children` keyed by slug — the URL path is the slug chain. The front
    page is whichever top-level node has slug "" (parallel to the other
    main level pages, not their parent); it cannot have children, and
    renaming its slug away leaves no front page ("/" redirects to the
    first nav item). `Node.content` is
    the Markdown page, or None for a pure category label whose URL renders
    a placeholder page (while nav links to it point at its first child);
    every label's title and slug are editable. Siblings order by the fractional `Node.order` key: a moved
    item gets a fresh key relative to its new siblings, all others keep
    theirs. `resolve`/`find_slot` walk the tree by path; moves are slot
    detach/attach carrying the whole subtree. Legacy flat `Data.pages`
    (pre-tree databases) migrates into `menu` on startup. The app owns
    the `Data` object; reads are plain attribute access, writes in
    `kanta.transaction(...)`.
    `Data.files` is a content-addressed store (blake3[:12] + extension)
    mapping file names to bytes, served at `/_f/{name}` with immutable
    caching; pages reference files by absolute `/_f/` URLs so hierarchy
    moves never break them. `Node.banner` is a raw trusted HTML snippet
    for the header banner (img, styled div, canvas+script...); empty
    inherits from the node's ancestors (front page last). It is rendered
    AFTER the banner design's artwork, so author code (e.g. a `<style>`
    override) always wins over the design's own styles.
    `Node.banner_design` picks a banner design: a theme folder name whose
    `banner.css`/`banner.svg` supply the design's styles and inline SVG
    artwork (marked `svg[data-design]`); "" = explicitly no design, None =
    inherit (nearest ancestor, front page last, then the active theme's
    own design if it ships banner.css/banner.svg). The design's banner.css
    is linked in `<head>` (id `pagerite-banner`) between the theme and the
    custom CSS.
    `Data.version` is bumped on every write
    and embedded in page ETags so nav-affecting changes invalidate caches.
    `Data.brand` is the site name (header link + `<title>` suffix), editable
    in the site editor via `/_api/settings`; empty = no header link and
    no `<title>` suffix. `Data.theme` is the active theme name (empty =
    none/base only); themes are folders in `pagerite/themes/{name}`
    containing `theme.css` and/or `banner.css` (+ `banner.svg` artwork),
    served by the backend at `/_themes/{name}/...` — read from disk per
    request (etag by mtime), never built, so on-disk edits show on the
    next page load even in prod. The theme selector and banner-design
    selector enumerate these folders via `GET /_api/settings`.
    `Data.custom_css` is raw trusted CSS injected inline in every page
    `<head>` (id `pagerite-user`) and swapped during fetch-navigation;
    editable in the site editor. Font picks (heading/body/brand) in the
    site editor are stored as plain `:root` rows in `custom_css`
    (`--font-body: var(--font-source-sans);` format — parsed out and
    rewritten on change, the `:root` block added/removed as needed),
    referencing the per-family variables (`--font-source-sans` etc.) from
    pagerite.css;
    the base stylesheet's `--font-brand` defaults to `var(--font-heading)`.
    `Data.favicon` names a file in the content-addressed `files` store,
    uploaded/cleared in the site editor via `PUT`/`DELETE
    /_api/settings/favicon`; when set it is linked as `<link rel="icon">`
    on every page, otherwise browsers fall back to the build's
    `/favicon.ico` by convention.
  - `markdown.py` — markdown-it-py renderer (html passthrough + attrs,
    footnote, deflist, tasklists plugins; typographer + breaks on). Custom
    image rule: relative srcs resolve against the page path, titled images
    become figures. A `{dates}` line expands to the article's
    published/updated dateline (`p.dateline`, from `Node.created`/
    `modified`; left literal in previews of unsaved pages).
  - `views.py` — the shared page layout as an html5tagger `Template` with
    placeholders (`Title`, `Brand`, `Banner`, `Nav`, `Sidebar`, `Main`), nav
    rendering straight from the `Data.menu` tree (siblings sorted by
    `Node.order`; nav links to content-less labels point at their first
    child via `first_leaf`), and page/404 rendering. If the markdown contains its own h1, the page title
    is NOT rendered as an additional h1 (it still supplies <title> and nav
    labels). The navbar holds
    top-level items only; the current section's subitems go to a left
    `#sidebar`, which is rendered when the section offers at least two
    published items, or exactly one while viewing anything other than that
    only page — the section index, a 404, a grandchild (so those pages can
    reach the child); no aside element at all on the front page, leaf
    pages and the sole page of a one-page section. Also,
    category labels are nodes without content — None *or* empty markdown —
    and their nav links point at their first child page. Dynamic regions have stable ids
    (`#page-banner`, `#nav`, `#sidebar`, `#main`) for fetch-navigation swaps
    (`#sidebar` may be absent on either side of a swap).
  - `seed.py` — demo content written on startup for paths missing from the
    database (never overwrites existing pages).
  - `frontend/src/` — the Vue editor and public-page entries.
    - `main.js` — Vue editor app entry, mounts PageEditor/SiteEditor.
    - `pagerite.js` — public page entry; runs fetch-navigation, scroll-reveal,
      brand shrink-to-fit (the themed size is the maximum; JS reduces the
      font-size so a long brand or narrow viewport still fits one line),
      code copy buttons, and the auth check. It first probes `GET /auth/api/settings`
      to detect whether Paskia SSO is available, then `GET /_api/settings` to
      learn the current session's admin status. The same reverse proxy that
      gates `/_api` returns 401 for anonymous users, 403 for users without
      the admin permission, and 200 for admins. When Paskia is detected, a
      🔑 login button (anonymous) or 👤 profile button (logged in) is shown in
      the banner corner; both open Paskia's iframe dialog via `showAuthIframe`
      instead of navigating away. Admins also get the 🖊️ edit pens (asset URLs
      from the `pagerite:editor-src`/`-css` meta tags). If no Paskia SSO is
      detected (dev/no proxy), editing is left open. Pages themselves render
      identically for everyone; the real gate is the auth proxy in front of
      all of `/_api`. The backend links the stylesheets in a fixed order —
      base (Vite build), theme, banner design, custom CSS last — each with
      a stable id so the site editor can swap them in place.
    - `assets/` — shared styles and data files built by Vite and served hashed
      under `/_assets/`: `pagerite.css` (base layout + conservative
      variables), `pygments.css`,
      and `fonts/` (self-hosted Source
      Sans 3/Source Serif 4/Fraunces/Literata/Cormorant/Playfair
      Display/Inter/Montserrat/Fira Code/Cause/Exo 2/New Rocker
      variable woff2). The `::view-transition*` block at the end of `pagerite.css` (from
      termotohtori.fi) is fragile — do not tweak. Themes are NOT built:
      `pagerite/themes/{name}/theme.css` (theme overrides and font picks:
      `purple` = dark dusk palette with Fraunces/Literata and a tilted
      oversized gradient brand; `corporate` = light-first with automatic
      `prefers-color-scheme` dark mode, Montserrat/Inter and a huge solid
      brand; `nitro` = racing/HUD style following `prefers-color-scheme`
      (warm light-grey page, deep violet in dark), Montserrat/Literata,
      black as an accent only, a straight orange blade under the banner, and
      an orange racing-tab nav clipped with a bezier `shape()`) and the
      companion `banner.css` banner designs are served by the backend.
    - Vite builds ES-module `.js` outputs; the backend renders `<script
      type="module">` for them (module scripts defer by default).
  - The database file is `pagerite.kantadb` in the cwd (`PAGERITE_DB`
    overrides); gitignored. Do not delete it without asking.
- `scripts/fastapi-vue/` — helper scripts from the fastapi-vue template
  (build hook etc.), do not edit.
- `frontend/` — the Vue editor as **two separate apps** mounted in their
  own host divs created inside the static document: `PageEditor.vue`
  (CodeMirror + server-rendered preview over WebSocket `/_api/ws/editor`,
  previewing into the visible article; editor scroll drives document
  scroll) opened by the article pen — it edits content and title only,
  never the path — and `SiteEditor.vue` (site brand + theme selector +
  favicon upload/remove + site-wide custom CSS + per-page banner design
  selector (inherit/none/named design, inherited by children) + banner
  HTML edited in small CodeMirror windows;
  banner previewed into `#page-banner`, CSS injected into
  `<head id="pagerite-user">`) + vue-draggable structure tree with
  always-editable title/slug inputs per row, opened by the banner pen —
  everything saves immediately as you edit (brand/title/CSS debounced,
  slug on commit since it renames the path), theme change swaps the
  stylesheet in place, tree rows navigate in place without transitions when
  focused, and the front page is a root-only row whose empty slug is
  editable like any other. Every
  non-empty list (and the root) ends with a non-draggable ➕ footer row
  (vuedraggable `#footer` slot): clicking it starts a new pending page at
  that level (its slug placeholder shows the slug derived live from the
  title being typed), and while dragging it is the list's "end of list" drop
  target. Committing a pending page PUTs it with empty markdown (creates
  an empty page that renders with its title — saving never deletes;
  deletion is the page editor's explicit choice: saving trimmed-empty
  text issues a REST DELETE), then hands over to the page editor
  (CodeMirror focuses on mount). Dropping ON the lower part of a row
  moves the page under that
  row (the child list's container invisibly overlaps its own row's bottom
  via negative margin — Sortable inserts it as the first child natively),
  while a row's exposed top edge inserts a sibling before it. Row
  indentation is structural (each nested list margin-indents itself), so a
  dragged row previews its whole subtree at the target list's depth. The
  two pens swap the docked
  panel for the other editor; clicking the open editor's own pen closes it. Normally dynamic-imported onto the content page by
  pagerite.js when a 🖊️ edit pen is clicked (the pens are injected by
  pagerite.js after the session validates; they carry
  `data-editor-src`/`data-editor-css`/`data-editor-mode`).
  In dev, modules load from the Vite dev server (`PAGERITE_VITE_URL`),
  in prod from the hashed build assets resolved via
  `frontend-build/.vite/manifest.json`. `vite.config.js` sets
  `appType: 'mpa'` (no SPA fallback) and builds with `manifest: true`,
  `assetsDir: '_/assets'` (so the build mirrors the URL space;
  `frontend/public/favicon.ico` lands at the build root and is served at
  `/favicon.ico`). JS inputs are `src/main.js` and `src/pagerite.js`, plus
  `src/assets/pagerite.css` as a separate stylesheet entry; theme and
  banner-design CSS are NOT built — they live in `pagerite/themes/{name}/`
  and are served by the backend. There
  is no `index.html` source (it would shadow `/` and turn missing dev paths
  into an empty Vue shell). All outputs are ES modules. The build sets
  `preserveEntrySignatures: 'exports-only'` because main.js is consumed
  via dynamic `import()` for its `openEditor`/`closeEditor` exports — Vite
  app builds otherwise strip unused entry exports, leaving dead edit pens.
  In dev the backend links theme/banner-design stylesheets like in prod
  (`/_themes/...`); only the base CSS is Vite-injected from JS, and
  pagerite.js then re-appends the `#pagerite-theme`/`#pagerite-banner`/
  `#pagerite-user` elements to restore the canonical order (base < theme <
  design < custom CSS). Theme switches in the site editor simply swap the
  `#pagerite-theme` link href, identically in dev and prod.
  vite-plugin-fastapi.js has an
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
    **Pygments** for server-side code highlighting (`nowrap` spans, styled
    by `frontend/src/assets/pygments.css` which maps token classes 1:1 onto
    the `--code-*` variables; light/dark palette sets live in
    `pagerite.css` and resolve via `light-dark()` from the theme's
    `color-scheme` — themes pick a set, not individual colors).

## Conventions

- Keep dependencies minimal; add via `uv add` and mention it.
- The public URL space belongs to content (pretty slugs at root). Reserve
  only `/_` for the machinery (`/_api/`, `/_f/`, `/_assets/`), plus
  `/favicon.ico` from the build. Slugs are lowercase ASCII letters, digits,
  hyphens and underscores `[a-z0-9_-]` (the site editor filters input live
  via `slugify.js`, built on the `transliteration` npm package — unicode
  folds to ASCII, spaces become hyphens; an empty slug on a new page is
  derived from its title), may not begin with `_` or `.`, and such URLs are
  never looked up as content.
- No auth in core code; the SSO/reverse proxy gates all of `/_api`
  (forward-auth) and owns `/auth/` (login/logout, session validation).
  Pages render identically for everyone; pagerite.js adds the editing UI
  only after the auth server validates the session. Never add output
  sanitization "for safety" against the author — embedded HTML/scripts in
  Markdown are passed through deliberately.
- Update this file and `docs/design-principles.md` when architecture,
  tooling, or conventions change.
