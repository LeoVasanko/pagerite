# Content model

The site structure is stored in the kanta database managed by `pagerite/data.py`.

## Site tree

`Data.menu` maps top-level slugs to `Node`s, each with `children` keyed by slug — the URL path is the slug chain. The front page is whichever top-level node has slug "" (parallel to the other main level pages, not their parent); it cannot have children, and renaming its slug away leaves no front page ("/" redirects to the first nav item).

`Node.content` is the Markdown page, or None for a pure category label whose URL renders a 404 listing its children as cards (while nav links to it point at its first child); every label's title and slug are editable. A page with published children — a category page — lists them as cards after its markdown content; the sidebar sub-navigation renders only from the second level down, never on main-level pages.

Siblings order by the fractional `Node.order` key: a moved item gets a fresh key relative to its new siblings, all others keep theirs. `resolve`/`find_slot` walk the tree by path; moves are slot detach/attach carrying the whole subtree. Legacy flat `pages` (pre-tree databases) migrates into `menu` via `migrate_v1`. The app owns the `Data` object; reads are plain attribute access, writes in `kanta.transaction(...)`.

Every content/settings write calls `_invalidate_pages()` in app.py, which clears the rendered-body LRU and bumps an in-memory render generation embedded in page ETags, so nav-affecting changes invalidate caches. (This used to be a persisted `Data.version` counter — cache invalidation is not database state, so the field was dropped; old databases lose the key on re-serialization.)

## Files

Files are content-addressed (blake3[:12] + extension) and stored **on disk** under `<hostname>/files/` (path from `PAGERITE_FILES`), served at `/_f/{name}` with immutable caching. Uploaded raster images (except GIF) and SVGs (rasterized) get a set of derivatives: the untouched original under `<hash>.orig<ext>` (internal only — it may carry EXIF data and is never served; SVG originals stay servable as `<hash>.svg`), a mediapreview-recompressed AVIF (`<hash>.avif`, thumbnailed to `IMAGE_MAXSIZE` at `IMAGE_QUALITY`), and WebP/JPEG fallbacks re-encoded from the AVIF at lower quality (`IMAGE_WEBP_QUALITY`/`IMAGE_JPG_QUALITY`, chosen for similar-or-smaller file size). Pages link the bare `/_f/<hash>` and the server negotiates by Accept header: a format is served only when listed explicitly (`image/avif` → AVIF, `image/webp` → WebP, anything else including `image/*` and `*/*` → JPEG); an explicit extension in the URL pins the format. Responses carry `vary: accept`. Favicons uploaded in settings go through the same pipeline at `FAVICON_MAXSIZE` (192px). Existing databases are updated by `migrate_v2` (link rewrite plus on-disk derivative backfill). Deleting any name of a hash removes the whole group. The `FileStore` in app.py caches every file in RAM, both uncompressed and zstd-compressed (the compressed copy only when smaller), so `/_f` answers both encodings without disk reads. Pages reference files by absolute `/_f/` URLs so hierarchy moves never break them. Pre-refactor databases kept the blobs in a `Data.files` kanta field; the kanta migration `pagerite/migrations.py::migrate_v1` writes them to disk on open and drops the field (removed from `Data`). Fetched favicons of external analytics sites live in the same store (see `docs/analytics.md`).

## Banners

`Node.banner` is a raw trusted HTML snippet for the header banner (img, styled div, canvas+script...); empty inherits from the node's ancestors (front page last). It is rendered AFTER the banner design's artwork, so author code (e.g. a `<style>` override) always wins over the design's own styles.

`Node.banner_design` picks a banner design: a theme folder name whose `banner.css` styles it and whose `banner.html` (arbitrary markup: canvas + style + script) or `banner.svg` supplies the inline artwork (wrapped in `div[data-design]`); "" = explicitly no design, None = inherit (nearest ancestor, front page last, then the active theme's own design if it ships banner.css/banner.svg/banner.html). The design's banner.css lives in `<head>` (id `pagerite-banner`) between the theme and the custom CSS — a `<link>` in dev, an inline `<style>` in production.

## Site settings

`Data.brand` is the site name (header link + `<title>` suffix), editable in the site editor via `/_api/settings`; empty = no header link and no `<title>` suffix.

`Data.brand_html` is raw trusted HTML replacing the brand link entirely (rendered in a `#brand` div on top of the banner, next to the nav) — site-wide, not per-page like banners; edited in the site editor with image/video upload into the content-addressed file store.

`Data.theme` is the active theme name (empty = none/base only); themes are folders in `pagerite/themes/{name}` containing `theme.css` and/or `banner.css` (+ `banner.svg` artwork and any extra assets the CSS references, like summer's `grass.svg`), served by the backend at `/_themes/{name}/...` — read from disk per request (etag by mtime), never built, so on-disk edits show on the next page load even in prod. The theme selector and banner-design selector enumerate these folders via `GET /_api/settings`.

`Data.transition` is the page-transition design name (default `cube`): a theme folder shipping `transition.css`, injected as `#pagerite-transition` on every page and selected in the site editor (the selector enumerates `transition.css` folders via `GET /_api/settings`). See `docs/themes-and-assets.md`.

`Data.custom_css` is raw trusted CSS injected inline in every page `<head>` (id `pagerite-user`) and swapped during fetch-navigation; editable in the site editor. Font picks (heading/body/brand) in the site editor are stored as plain `:root` rows in `custom_css` (`--font-body: var(--font-source-sans);` format — parsed out and rewritten on change, the `:root` block added/removed as needed), referencing the per-family variables (`--font-source-sans` etc.) from `pagerite.css`; the base stylesheet's `--font-brand` defaults to `var(--font-heading)`.

`Data.favicon` names a file in the content-addressed store (on disk under `<hostname>/files/`), uploaded/cleared in the site editor via `PUT`/`DELETE /_api/settings/favicon`; when set it is linked as `<link rel="icon">` on every page, otherwise browsers fall back to the build's `/favicon.ico` by convention.
