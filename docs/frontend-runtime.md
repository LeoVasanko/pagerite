# Frontend runtime

The public page runtime lives in `frontend/src/`.

## `main.js`

Vue editor app entry, mounts the tabbed `EditorShell`. See `docs/editing.md` for the editor UI.

## `pagerite.js`

Public page entry; runs fetch-navigation (backed by an in-memory page cache: every visible internal link — and the current page — is fetched once at load, clicks are then served from JS with no fetch, and the editors' `loadPlain` keeps the cache current via a `pagerite:page-fetched` event; articles are `cache-control: no-cache` on the wire), scroll-reveal, OverlayScrollbars on `document.body` (floating, auto-hiding scrollbars that never reserve layout space or shift the page when appearing; native scroll APIs like `window.scrollTo` keep working; themed via the `--os-*` variables in pagerite.css), brand shrink-to-fit (the themed size is the maximum; JS reduces the font-size so a long brand or narrow viewport still fits one line), code copy buttons, and the auth check.

It first probes `GET /auth/api/settings` to detect whether Paskia SSO is available, then `GET /_api/settings` to learn the current session's admin status. The same reverse proxy that gates `/_api` returns 401 for anonymous users, 403 for users without the admin permission, and 200 for admins. When Paskia is detected, a login link (anonymous) or profile link (logged in) is shown in the banner corner; both are plain `<a href="/auth/">` links (Paskia does not support being iframed, so we navigate normally), and a `pageshow` handler re-probes auth when history navigation restores a cached page. Admins also get the page/banner edit pens and a site-settings pen (asset URLs from the `pagerite:editor-src`/`-css` meta tags). If no Paskia SSO is detected (dev/no proxy), editing is left open. Pages themselves render identically for everyone; the real gate is the auth proxy in front of all of `/_api`. The backend links the stylesheets in a fixed order — base (Vite build), theme, banner design, custom CSS last — each with a stable id so the site editor can swap them in place.

## `assets/`

Shared styles and data files built by Vite and served hashed under `/_assets/`: `pagerite.css` (base layout + conservative variables), `pygments.css`, and `fonts/` (self-hosted Source Sans 3/Source Serif 4/Fraunces/Literata/Cormorant/Playfair Display/Inter/Montserrat/Fira Code/Cause/Exo 2/New Rocker variable woff2).

The `::view-transition*` block at the end of `pagerite.css` (from termotohtori.fi) is fragile — do not tweak. Themes and banner designs are NOT built — they live in `pagerite/themes/{name}/` and are served by the backend. See `docs/themes-and-assets.md` for details.

Vite builds ES-module `.js` outputs; the backend renders `<script type="module">` for them (module scripts defer by default).

## Database file

The database file is `pagerite.kantadb` in the cwd (`PAGERITE_DB` overrides); gitignored. Do not delete it without asking.
