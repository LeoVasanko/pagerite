# Pagerite Design Principles

Pagerite is a single-user CMS/blog. This document records the initial
high-level design decisions; it will be refined as the implementation
evolves.

## Architecture

- **Server-side rendered.** FastAPI serves complete HTML pages, generated in
  Python with **html5tagger**. There is no client-side templating or SPA for
  the public site.
- **Vue only where interactivity demands it.** Small interactive islands
  (editing tools mainly) may be Vue components, either mounted into specific
  elements of the server-rendered pages or served as standalone apps
  (e.g. an admin panel). The public reading experience has no scripting
  requirement.
- **Persistence via kanta.** Content is stored in an asyncio-friendly kanta
  database. Rendering happens on the fly on each request — there are no
  pre-built static artifacts.

## Content model

- Pages and blog articles are fundamentally the same kind of thing: named
  pieces of content. The blog/website distinction is blurred; an article is
  just a page (possibly with metadata such as a publication date and
  listing in a feed).
- **Pretty URLs.** Content is addressed by its name (slug), not by technical
  constructs — no `/cms/...` or `/blog/post1` prefixes. Slugs usually live
  directly at the site root; structured content may nest
  (`/docs/design-principles`-style). The URL space is the author's, so
  reserved prefixes must be kept few and deliberate: everything internal
  lives under `/_/` (the API at `/_/api/`, uploaded files at `/_/f/`, built
  assets at `/_/assets/`, and the admin shell at `/_/admin`). The only
  other reserved root path is `/favicon.ico`, served from the build.
  Slugs are lowercase ASCII (`[a-z0-9-]`; input is transliterated and
  filtered as you type, and a new page's empty slug is derived from its
  title), may not begin with `_` or `.`, and may not occupy a reserved
  root file name (`robots.txt`, `sitemap.xml`, `favicon.ico`, …) — such
  URLs are never looked up as content.
- **Single user, trusted author.** No auth concerns in the core design.
  Everything published is public; only editing tools will later sit behind
  access control (external SSO when that time comes). The author is trusted
  to create well-meaning slugs and content — no sanitization for safety,
  only for correctness.
- **Commenting** is not planned now but the model should not preclude it
  later.

## Authoring format

- Content is written in **Markdown** with powerful extensions (tables,
  footnotes, code highlighting, etc.).
- **Embedded HTML is passed through unfiltered**, including inline scripts
  and other dynamic content the author wants to post. This is safe by the
  single-trusted-author assumption above.
- Renderer: **markdown-it-py** with mdit-py-plugins (footnotes, definition
  lists, task lists, brace-attributes; tables and strikethrough from the
  default preset), with `html=True` for raw passthrough. Fenced code blocks
  are highlighted server-side with **Pygments** (github-dark palette in
  `/_/assets/pygments-*.css`); a JS copy button appears on hover. Should this
  prove limiting, we implement our own renderer on top of html5tagger,
  which we already use for all HTML generation.
- **Files are content-addressed.** Uploads (`PUT /_/api/files/{filename}`)
  are stored by content hash — blake3, first 6 bytes hex + original
  extension — and served immutable from `/_/f/{hash}.ext`. Absolute URLs
  that survive page renames and dedupe identical content; pages no longer
  own files. An image with a title becomes a `<figure>` with
  `<figcaption>`. Positioning is by attribute classes:
  `![alt](/_/f/….avif "Caption"){.right}` — `{.right}`, `{.left}` float,
  `{.wide}` goes full bleed (viewport edge to edge, or up to the docked
  editor; the sidebar stacks on top of it); plain attributes like `width=300`
  work too.

## Page structure and navigation

- All pages share one static layout, defined once as an **html5tagger
  Template** with capitalized placeholders (`Title`, `Banner`, `Nav`,
  `Sidebar`, `Main`) filled per request. The dynamic regions carry stable
  ids (`#page-banner`, `#nav`, `#sidebar`, `#main`).
- The page top is a **full-width banner header** with the site name and the
  navigation bar overlaid on it — no separate chrome header. The banner is
  **per-page configurable**: `Node.banner` holds an arbitrary trusted HTML
  snippet (an image, a styled div, canvas + script — anything), resolved by
  walking up the node's ancestors to the front page; when nothing in the
  chain sets one, the default `banner.svg` artwork (inlined into the
  stylesheet by the build) shows.
- **Fetch-navigation.** Links are plain `<a href>`; a small script
  (`frontend/src/pagerite.js`) intercepts same-origin clicks, fetches the
  page, and swaps the `#page-banner`, `#nav`, `#sidebar` and `#main` regions
  and the document title, keeping `<head>` and the layout chrome. Without JS
  everything works as normal page loads. Scripts inside fetched banner and
  content regions are re-created so they execute. Swaps run inside `document.startViewTransition` for a rotating
  cube page transition (CSS adapted from termotohtori.fi — the
  `::view-transition*` block is fragile, do not tweak; skipped under
  `prefers-reduced-motion`). Navigation within the same top-level section
  crossfades instead of rotating; browser back navigation rotates in
  reverse.
- **The site structure is a tree of labels.** `Data.menu` holds the
  top-level items by slug, each with `children` keyed by slug — the URL
  path is the slug chain. The front page is a top-level node with slug ""
  (an item *parallel* to the other main level pages, not their parent) and
  cannot have children. The header navbar holds only the top level; a
  top-level item is highlighted when viewing any of its subpages. When the
  current page is inside a main level section with children, those direct
  children are listed in a **left sidebar** (`#sidebar`), one level deep;
  the sidebar is empty (and hidden) elsewhere. Other sections' subitems
  are never shown without navigating into them first.
- **Landing pages are optional.** Every label can either have content
  (`Node.content`, a Markdown page) or none — a content-less label
  redirects to its first child instead of 404ing, so categories need no
  filler content. Title and slug of every label are editable; renaming a
  slug moves the whole subtree. The sidebar never lists the section
  itself, avoiding title duplication with the navbar.
- **Menu order is manual.** Each node has a fractional `order` key among
  its siblings; reordering/moving writes only the moved node (it takes a
  fresh value halfway between its new siblings; all other items keep
  theirs). New pages append at the end of their menu. Structure edits
  (reorder, move/rename with the whole subtree, retitle) go through
  `POST /_/api/structure` and the editor's structure panel.
- Unpublished pages are hidden from both nav and URL access (404).

## Reading experience

- The article column is sized by the **viewport, never by content**: a
  symmetric grid (`1fr minmax(0, 78rem) 1fr`) with flexible gutters keeps
  the layout stable across navigation. The sidebar occupies the left
  gutter, the right gutter balances it; wide screens get columns inside
  long articles without changing the article's width.
- A gentle **scroll-reveal** of headings, figures and block-level elements
  (IntersectionObserver). It is layout-level: articles need no support
  for it, and `prefers-reduced-motion` disables all motion.

## Styling

- A single shared `frontend/src/assets/style.css` covers the server-rendered
  pages and the Vue components. Vue may add per-component styles on top where
  needed.
- Fonts, the shared stylesheet, pygments styles and the default banner SVG
  live under `frontend/src/assets/` and are emitted as hashed assets under
  `/_/assets/` (Fraunces for headings, Literata for body, Fira Code for code —
  variable woff2 files with local `@font-face`). No third-party requests.

## Editing

- Editing happens **in place**, in two modes opened by two pens:
  - **Page mode** — the 🖊️ next to a page's heading (including 404s, which
    is how new pages start) opens a CodeMirror Markdown editor docked to
    the left of the article: the host sits inside `#content` (below the
    banner, never over the footer), the content shifts right and the
    sidebar hides while editing. Preview renders server-side per keystroke
    (no debouncing) straight into the visible article's heading and body.
  - **Site mode** — the 🖊️ on the banner opens a panel with the site
    **brand** (applied to the header live), the page's **banner HTML**
    field (previewed into the real banner region, so you see exactly
    which banner you're editing) and the **structure tree**. Everything
    saves immediately as you edit — no save button, no edit mode.
- Clicking a pen again closes the editor (without saving; a dirty preview
  reloads the page). The pens are `<button>`s wired up by `pagerite.js` —
  editing is an action, not a navigation. The editor's WebSocket
  **reconnects automatically** with local text and pending saves preserved.
  A standalone shell also exists at `/_/admin#/path` with its own preview
  pane. (All users are trusted authors for now; access control later with
  SSO.)
- **CodeMirror 6** for Markdown editing (no WYSIWYG), title/published
  controls.
  Images can be pasted straight into the editor or chosen via a file
  input: they upload to the content store (`PUT /_/api/files/...`) and
  insert `![alt](/_/f/hash.ext)` at the cursor.
- The **structure panel** (vue-draggable tree of the whole site, in site
  mode) covers page management: reorder any menu level, drag across
  sections (empty child lists appear as drop zones while dragging), add,
  delete (two clicks: the button arms, then deletes — no dialogs).
  Every node is a real label — content-less category rows offer a
  ➕ to give them a landing page.
  Deleting a category removes only its landing page (the label and its
  subpages stay). The ➕ in the panel header starts a new page as a
  local-only tree row that can be dragged into place before its title and
  slug are filled in; it is persisted only on commit. Rows are always
  editable: titles save while typing, slug edits commit on blur/Enter
  since they rename the path (moving the whole subtree). The front page
  is the root row with an empty slug — renaming it away leaves no front
  page ("/" redirects to the first nav item), and giving another
  top-level row the empty slug makes it the front page.
- Preview and saving go over a **WebSocket** (`/_/api/ws/editor`) with a
  stateless JSON protocol (`open`/`render`/`save`; on save all fields are
  optional and absent ones keep their old values, `move_from` renames),
  avoiding REST polling and races. Rendering always stays server-side.
- A REST API also exists for scripting, all under `/_/api/`:
  `GET pages` (the full tree), `PUT/DELETE pages/{path}`,
  `GET/PUT settings` (site brand), `POST structure` (reorder/move/
  retitle), file upload/removal via `PUT/DELETE files/{name}`.
- On startup, seed pages from `pagerite/seed.py` are added **only if
  missing** — existing user content is never overwritten.
