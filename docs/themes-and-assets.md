# Themes and assets

## Built assets

Files under `frontend/src/assets/` are built by Vite and served hashed under `/_assets/`:

- `pagerite.css` — base layout + conservative variables.
- `pygments.css` — Pygments token styles mapped onto the `--code-*` variables.
- `fonts/` — self-hosted variable woff2 files for Source Sans 3, Source Serif 4, Fraunces, Literata, Cormorant, Playfair Display, Inter, Montserrat, Fira Code, Cause, Exo 2 and New Rocker.

The `::view-transition*` block at the end of `pagerite.css` (from termotohtori.fi) is fragile — do not tweak.

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

## Stylesheet order

The backend links the stylesheets in a fixed order — base (Vite build), theme, banner design, custom CSS last — each with a stable id so the site editor can swap them in place. The base stylesheet's `--font-brand` defaults to `var(--font-heading)`.
