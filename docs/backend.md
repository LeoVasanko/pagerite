# Backend

The Python backend lives in `pagerite/`.

## `app.py`

The FastAPI app. FastAPI's built-in API docs are disabled (`docs_url`/`redoc_url`/`openapi_url=None`) because `/docs` belongs to our content. Our own routes (content pages, `/_api/...`, `/_f/...`) are registered BEFORE `frontend.route(app, "/")` is called: fastapi-vue inserts its file routes at the position where `route()` was called (during `load()` in the lifespan), so anything defined earlier wins. The one exception is the content catch-all `/{path:path}`, registered AFTER `frontend.route()` so that built frontend assets still take priority over content slugs. The `Frontend` is constructed with `spa=False` explicitly: it only serves the built files without a catch-all.

The build mirrors the URL space — hashed immutable assets under `/_assets/`, `favicon.ico` at the site root — and an `index.html` in the build would become a `/` route, so leave it out of the build to keep `/` ours.

Generated HTML pages (content pages, category/404 placeholders, `/_a`) go through `_html_response`: zstd-compressed per request at level 9 when the client sends `accept-encoding: zstd` (no gzip fallback; static assets are pre-compressed by the `Frontend`), with `vary: accept-encoding` set and the ETag kept identical across encodings so `if-none-match` revalidation still works. In production the rendered bodies are cached in an LRU keyed by everything the output depends on — page kind, path, the site origin (social meta), encoding, and `data.version`, which bumps on every content/settings change and so transparently invalidates the whole cache. The cache is bypassed in dev, where theme/design CSS is re-read from disk per request. Content pages carry an ETag built from the node's modified timestamp and `data.version`; `/_a` instead gets a blake3 hash of the rendered body (it has no Node), with matching `if-none-match` revalidations answered by a 304.

Uploaded files, seed assets and fetched external-site favicons live in the `FileStore`: content-addressed files on disk under `<hostname>/files/` (`PAGERITE_FILES`), fully cached in RAM at startup — both the raw body and a zstd-compressed copy (kept only when smaller). `GET /_f/{name}` serves from the RAM cache with immutable caching, answering the zstd variant when the client accepts it; the name is the ETag. Legacy databases that still carry blobs in a `files` kanta field are migrated to disk by `pagerite/migrations.py::migrate_v1` (kanta's `migrate_vN` mechanism, wired via `Kanta(..., migrations="pagerite.migrations")`), which pops the field from the raw state before struct decoding.

## `data.py`

msgspec Structs for the kanta database. See `docs/content-model.md` for the full data model.

## `markdown.py`

markdown-it-py renderer (html passthrough + attrs, footnote, deflist, tasklists, admon, gfm_autolink, sub/superscript plugins; typographer + breaks on). In bodies with at least three in-body h1/h2 headings, each gets a slug id (`python-slugify`, mirroring the editor's `slugify.js` — unicode folds to ASCII, separators become single hyphens) unless the author set `{#id}`, and their text is wrapped in a self-link (`a.anchor`) so section links are copyable; the first in-body h1 is the article title — when the markdown has no h1, `render(title=...)` injects it as `# {title}` so implicit and explicit titles take the same path — it gets no id and doesn't count toward the three, its self-link is `href=""` (scroll to top); shorter articles stay anchor-free, h3+ is never navigable, and duplicates get `-2`/`-3` suffixes. Custom image rule: relative srcs resolve against the page path; an image standing alone in its paragraph becomes a figure (captioned when titled), while inline-with-text images and raw `<img>` HTML stay plain. A `{dates}` line expands to the article's published/updated dateline (`p.dateline`, from `Node.created`/`modified`; left literal in previews of unsaved pages).

`render()` returns a `Rendered(html, multicol)`: the article content segmented for the column layout (there is no wrapper div — segments and bare blocks are direct `<article>` children) — h1/h2 headings, `.wide` blocks and margin-breakout blocks (`.margin`, `::: aside`) stand bare, the runs between them become `<div class="colseg">` (plus `.cols` on segments with enough text, `::: nocols` opting out), and `multicol` flags bodies long enough to columnize (visible-text thresholds, code excluded). `views.py` puts the class on the article; pagerite.css takes it from there (at most two columns, the left-margin breakout, all viewport adaptation).

## `views.py`

The shared page layout as an html5tagger `Template` with placeholders (`Title`, `Brand`, `Banner`, `Nav`, `Sidebar`, `Main`), nav rendering straight from the `Data.menu` tree (siblings sorted by `Node.order`; nav links to content-less labels point at their first child via `first_leaf`, the first published descendant with content), and page/404 rendering.

Content pages get SEO/social meta (description, canonical link, Open Graph + twitter card) from heuristics over the rendered article: the description is the first paragraph's text, the share image prefers a `{.hero}`-classed image, then the first raster `<img>`, then the first SVG; the first `<video>` yields `og:video`; URLs are made absolute with the site origin (`SITE_URL` — `https://<hostname>` from the CLI hostname argument; on localhost the request's own base URL is the fallback); `article:published/modified_time` come from `Node.created`/`modified`. The page title is injected as `# {title}` when the markdown has no h1 of its own, so it never appears twice (it always supplies `<title>` and nav labels).

The navbar holds top-level items only; the current section's subitems go to a left `#sidebar` as a nested list (the section's direct children plain, deeper levels indented with article-list-style markers), rendered only from the second level down — main-level pages list their children as cards after the content instead. Below that, the sidebar renders when the section offers at least two published items, or exactly one while viewing anything other than that only page — the section index, a 404, a grandchild (so those pages can reach the child), and also on that only page itself when it has published children of its own; no aside element at all on the front page, main-level pages, leaf pages and the sole childless page of a one-page section. Also, category labels are nodes without content — None *or* empty markdown — and their nav links point at their first child page. Dynamic regions have stable ids (`#page-banner`, `#nav`, `#sidebar`, `#main`) for fetch-navigation swaps (`#sidebar` may be absent on either side of a swap).

Any page with published children — a category page — lists them as a card grid (`nav.cards`) after the markdown content, as does the content-less category 404. Each card links to the child page (a content-less child to its first leaf) and shows the child's share image (the same hero → first raster → first SVG heuristics as `og:image`) as a full-card cover with the title overlaid.

## `seed.py`

Demo content written only when the database is first created, via a `@kanta.bootstrap` handler in `app.py`.
