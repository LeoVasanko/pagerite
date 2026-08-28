# Themes and assets

## Built assets

Files under `frontend/src/assets/` are built by Vite and served hashed under `/_assets/`:

- `pagerite.css` — base layout + conservative variables.
- `pygments.css` — Pygments token styles mapped onto the `--code-*` variables.
- `fonts/` — self-hosted variable woff2 files for Source Sans 3, Source Serif 4, Fraunces, Literata, Cormorant, Playfair Display, Inter, Montserrat, Fira Code, Cause, Exo 2 and New Rocker.

The `::view-transition*` rules are not in the base stylesheet: they live in the page-transition designs (`pagerite/themes/{name}/transition.css`, see below).

## Themes

Themes are folders in `pagerite/themes/{name}/` containing `theme.css` and/or `banner.css` (+ `banner.svg` artwork and any extra assets the CSS references, like summer's `grass.svg`). They are served by the backend at `/_themes/{name}/...` — read from disk per request (etag by mtime), never built, so on-disk edits show on the next page load even in prod.

`Data.theme` selects the active theme (empty = none/base only) and the site editor can switch it, choosing from the theme folders found on disk. Vue may add per-component styles on top where needed.

Current themes:

- `purple` — dark dusk palette with Fraunces/Literata and a tilted oversized gradient brand.
- `corporate` — light-first with automatic `prefers-color-scheme` dark mode, Montserrat/Inter and a huge solid brand.
- `nitro` — racing/HUD style following `prefers-color-scheme` (warm light-grey page, deep violet in dark), Montserrat/Literata, black as an accent only, a straight orange blade under the banner, and an orange racing-tab nav clipped with a bezier `shape()`.
- `summer` — light playful meadow, one palette sampled from its illustrated `banner.svg` (sky/grass/sun/flower pink), Fraunces/Literata, a tilted gradient brand, flower bullets, and a layered-parallax banner (sun rises, clouds drift, nearer hills move less) with idle animations (swaying flowers, floating clouds, breathing sun glow) wrapped in `prefers-reduced-motion: no-preference`.

## Banner designs

A theme folder may also ship a banner design (`banner.css` + `banner.html` arbitrary markup or `banner.svg`), selectable per page independently of the active theme. Standalone banner designs (no theme.css) ship as:

- `eyes` — a canvas critter in the grass.
- `stars` — a drifting starfield.

The banner artwork has scroll parallax: pagerite.js sets the `--pry` scroll parameter on `<html>` (event-driven, so it is still when the page is idle), the banner contents drift within their window (with scale overscan so no edge shows), and designs may key their own effects off the same parameter.

## Page transitions

A theme folder may ship a page transition (`transition.css`, `::view-transition*` rules), selected site-wide by `Data.transition` in the site settings and injected as `#pagerite-transition` (after the banner design). Standalone transition designs ship as:

- `cube` — rotating cube (from termotohtori.fi; the block is fragile — do not tweak), mirrored on history-back (`html.nav-back`), crossfading within a section (`html.nav-fade`).
- `slide` — plain sideways slide, old and new pages moving together; mirrored on history-back, crossfading within a section.
- `reveal` — clip-path wipe revealing the new page over the stationary old one; mirrored on history-back, crossfading within a section.
- `crossfade` — plain crossfade for all navigations.

pagerite.js toggles the `nav-back`/`nav-fade` classes on `<html>` around `document.startViewTransition` (skipped under `prefers-reduced-motion`); a transition design keys its `::view-transition*` rules off them as needed.

## Stylesheet order

The backend emits the stylesheets in a fixed order — base (Vite build), theme, banner design, page transition, entry sheets, custom CSS last — each with a stable id so fetch-navigation and the site editor can sync them in place. In dev they are `<link>`s (the base is Vite-injected from JS instead); in production they are inlined as `<style>` elements. The base stylesheet's `--font-brand` defaults to `var(--font-heading)`. Code text (Fira Code by default) is optically matched to the body font by x-height: `font-size-adjust: ex-height var(--code-x-height)` scales whatever code font is in use, so a theme that switches its body font sets `--code-x-height` to that font's x-height ratio (base: 0.478 for Source Sans 3; themes ship values for Inter, Montserrat, Literata and Cause).
