# Backend

The Python backend lives in `pagerite/`.

## `app.py`

The FastAPI app. FastAPI's built-in API docs are disabled (`docs_url`/`redoc_url`/`openapi_url=None`) because `/docs` belongs to our content. Our own routes (content pages, `/_api/...`, `/_f/...`) are registered BEFORE `frontend.route(app, "/")` is called: fastapi-vue inserts its file routes at the position where `route()` was called (during `load()` in the lifespan), so anything defined earlier wins. The one exception is the content catch-all `/{path:path}`, registered AFTER `frontend.route()` so that built frontend assets still take priority over content slugs. The `Frontend` is constructed with `spa=False` explicitly: it only serves the built files without a catch-all.

The build mirrors the URL space — hashed immutable assets under `/_assets/`, `favicon.ico` at the site root — and an `index.html` in the build would become a `/` route, so leave it out of the build to keep `/` ours.

## `data.py`

msgspec Structs for the kanta database. See `docs/content-model.md` for the full data model.

## `markdown.py`

markdown-it-py renderer (html passthrough + attrs, footnote, deflist, tasklists, admon, gfm_autolink, sub/superscript plugins; typographer + breaks on). Custom image rule: relative srcs resolve against the page path; an image standing alone in its paragraph becomes a figure (captioned when titled), while inline-with-text images and raw `<img>` HTML stay plain. A `{dates}` line expands to the article's published/updated dateline (`p.dateline`, from `Node.created`/`modified`; left literal in previews of unsaved pages).

## `views.py`

The shared page layout as an html5tagger `Template` with placeholders (`Title`, `Brand`, `Banner`, `Nav`, `Sidebar`, `Main`), nav rendering straight from the `Data.menu` tree (siblings sorted by `Node.order`; nav links to content-less labels point at their first child via `first_leaf`, the first published descendant with content), and page/404 rendering.

Content pages get SEO/social meta (description, canonical link, Open Graph + twitter card) from heuristics over the rendered article: the description is the first paragraph's text, the share image prefers a `{.hero}`-classed image, then the first raster `<img>`, then the first SVG; the first `<video>` yields `og:video`; URLs are made absolute with the request base URL; `article:published/modified_time` come from `Node.created`/`modified`. If the markdown contains its own h1, the page title is NOT rendered as an additional h1 (it still supplies `<title>` and nav labels).

The navbar holds top-level items only; the current section's subitems go to a left `#sidebar` as a nested list (the section's direct children plain, deeper levels indented with article-list-style markers), which is rendered when the section offers at least two published items, or exactly one while viewing anything other than that only page — the section index, a 404, a grandchild (so those pages can reach the child), and also on that only page itself when it has published children of its own; no aside element at all on the front page, leaf pages and the sole childless page of a one-page section. Also, category labels are nodes without content — None *or* empty markdown — and their nav links point at their first child page. Dynamic regions have stable ids (`#page-banner`, `#nav`, `#sidebar`, `#main`) for fetch-navigation swaps (`#sidebar` may be absent on either side of a swap).

## `seed.py`

Demo content written only when the database is first created, via a `@kanta.bootstrap` handler in `app.py`.
