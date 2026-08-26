# Frontend runtime

The public page runtime lives in `frontend/src/`.

## `main.js`

Vue editor app entry, mounts the tabbed `EditorShell`. See `docs/editing.md` for the editor UI.

## `pagerite.js`

Public page entry; runs fetch-navigation (backed by an in-memory page cache: every visible internal link is fetched once at load and clicks are then served from JS with no fetch — the current page itself is not refetched, it enters the cache when navigated to — and the editors' `loadPlain` keeps the cache current via a `pagerite:page-fetched` event; articles are `cache-control: no-cache` on the wire). Editors can drop the entire cache with the `pagerite:drop-page-cache` event when site-wide or page changes (theme, headings, structure, banners, etc.) invalidate the cached HTML of other pages; `main.js` triggers a fresh `pagerite:preload-pages` pass when the editor panel closes so navigation is fast again. Navigation that starts while the editor is open bypasses the cache and fetches the target page on demand. Also runs scroll-reveal, OverlayScrollbars on `document.body` (floating, auto-hiding scrollbars that never reserve layout space or shift the page when appearing; native scroll APIs like `window.scrollTo` keep working; themed via the `--os-*` variables in pagerite.css), brand shrink-to-fit (the themed size is the maximum; JS reduces the font-size so a long brand or narrow viewport still fits one line), nav condense-to-fit (the top nav stays on one row: link gaps shrink first, then the side padding, then the font size; `flex-wrap: wrap` remains the no-JS fallback), code copy buttons, and the auth check.

It first probes `GET /auth/api/settings` to detect whether Paskia SSO is available, then `GET /_api/settings` to learn the current session's admin status. The same reverse proxy that gates `/_api` returns 401 for anonymous users, 403 for users without the admin permission, and 200 for admins. When Paskia is detected, a login link (anonymous) or profile link (logged in) is shown in the banner corner; both are plain `<a href="/auth/">` links (Paskia does not support being iframed, so we navigate normally), and a `pageshow` handler re-probes auth when history navigation restores a cached page. Admins also get the page/banner edit pens and a site-settings pen, plus a `modulepreload` warm-up of the editor bundle (the hashed asset is immutable, so it costs nothing). If no Paskia SSO is detected (dev/no proxy), editing is left open. Pages themselves render identically for everyone; the real gate is the auth proxy in front of all of `/_api`.

Asset wiring differs by mode. In dev the backend links the Vite dev-server URLs (`pagerite:editor-src`/`-css`/`pagerite:analytics-src` meta tags, `<link>` stylesheets) and Vite injects the entry CSS from JS for hot reloads. In production there are no pagerite meta tags: all page assets are inlined into the document — stylesheets as `<style>` elements in `<head>` (fixed order: base, theme, banner design, entry sheets, custom CSS last), module scripts as inline `<script>`s at the end of the body (relative chunk imports are rewritten to absolute `/_assets/` paths) — and the on-demand bundles' URLs ride in a `<script type="application/json" id="pagerite-assets">` config. The editor bundle always stays external, imported on demand when a pen is opened. Every stylesheet element carries a stable id so fetch-navigation and the site editor can sync `<head>` positionally across swaps (the analytics sheet exists on `/_a` only and is added/removed as you navigate). The analytics entry is inlined into the `/_a` page itself; pagerite.js re-creates that script element after fetch-navigating there (inline scripts don't execute on a DOM swap) and calls the module's exposed unmount before swapping away.

## `assets/`

Shared styles and data files built by Vite and served hashed under `/_assets/`: `pagerite.css` (base layout + conservative variables), `pygments.css`, and `fonts/` (self-hosted Source Sans 3/Source Serif 4/Fraunces/Literata/Cormorant/Playfair Display/Inter/Montserrat/Fira Code/Cause/Exo 2/New Rocker variable woff2).

The `::view-transition*` block at the end of `pagerite.css` (from termotohtori.fi) is fragile — do not tweak. Themes and banner designs are NOT built — they live in `pagerite/themes/{name}/` and are served by the backend. See `docs/themes-and-assets.md` for details.

Vite builds ES-module `.js` outputs; in dev the backend links them as `<script type="module">` (module scripts defer by default), in production it inlines them at the end of the body.

## Database file

The database file is `pagerite.kantadb` in the cwd (`PAGERITE_DB` overrides); gitignored. Do not delete it without asking.
