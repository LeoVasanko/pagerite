"""HTML rendering: page layout template, navigation, content pages.

All pages share one static layout, defined once as an html5tagger Template
with placeholders (capitalized attributes) filled per request. The dynamic
regions carry stable ids (#nav, #main) so that the fetch-navigation script
can swap them without reloading the page chrome.

Navigation walks the Node tree directly (see data.py): nav_html lists the
top level — the front page (slug "") is an ordinary top-level item, not
the parent of the others — and sidebar_html the sub-navigation of the
current top-level section, rendered only from the second level down
(main-level pages list their children as cards after the content instead,
see page_content). Nodes without content are category labels; nav links
to them point straight at their first child page (first_leaf), and their
own URL renders a card-listing page (render_category, a 404).
"""

from pathlib import Path
from html import unescape
import json
import os
import re

from html5tagger import HTML, Document, E, Template
from platformdirs import site_data_dir, user_data_path

from pagerite import i18n
from pagerite.data import Data, Node, node_markdown, prettify, resolve, sorted_nodes
from pagerite.i18n import Translation
from pagerite.markdown import render

SITE_NAME = "Pagerite"
BUILD = Path(__file__).with_name("frontend-build")


def _data_roots() -> list[Path]:
    """Platform data dirs for user-provided assets (platformdirs), most
    specific first: user data dir, then the system-wide data dirs."""
    roots = [user_data_path("pagerite", appauthor=False)]
    # site_data_dir keeps the multipath (site_data_path collapses it to the
    # first entry, since a Path cannot hold several).
    roots += site_data_dir("pagerite", appauthor=False, multipath=True).split(os.pathsep)
    return [Path(r) for r in roots]


def _theme_dirs() -> list[Path]:
    """Theme search roots, most specific first; first match wins per file.

    Users can add new themes or override/extend built-in ones file by file
    by placing folders in any of these roots (all combine into one listing):
    cwd, the site folder, and the platform user/system data dirs
    (e.g. ~/.local/share/pagerite, /usr/share/pagerite on Linux;
    %LOCALAPPDATA%\\pagerite, %PROGRAMDATA%\\pagerite on Windows).
    The built-in package themes are the final fallback.
    """
    return [
        Path("themes"),
        Path(os.getenv("PAGERITE_HOSTNAME", "localhost")) / "themes",
        *(root / "themes" for root in _data_roots()),
        Path(__file__).parent / "themes",
    ]


THEME_DIRS = _theme_dirs()

# User fonts are shared across themes, so they live in fonts/ folders next
# to the themes/ roots (no built-in fallback: built-in fonts ship with the
# Vite build). A font folder {name}/ carries font.css with @font-face rules
# (relative URLs resolve under /_fonts/{name}/) and a :root --font-{name}
# stack variable, so themes and custom CSS can reference it like the
# built-in --font-* variables.
FONT_DIRS = [
    Path("fonts"),
    Path(os.getenv("PAGERITE_HOSTNAME", "localhost")) / "fonts",
    *(root / "fonts" for root in _data_roots()),
]

# The base CSS is built by Vite as a separate entry so the backend can link
# it independently of the theme. Themes and banner designs are plain .css
# files in {THEME_DIRS}/{name}/, served by the backend at /_themes/{name}/...
# and re-read from disk on every request (see app.py), so they are never
# built and edits/new folders show without a restart.
_BASE_CSS_KEY = "src/assets/pagerite.css"

_manifest_cache: dict | None = None
_asset_cache: dict[str, tuple] = {}


def _manifest() -> dict:
    global _manifest_cache
    if _manifest_cache is None:
        _manifest_cache = json.loads((BUILD / ".vite/manifest.json").read_text())
    return _manifest_cache


def _theme_color_schemes(theme: str) -> set[str]:
    """Return the color-scheme keywords (``light``/``dark``) from theme.css.

    Reads the first ``color-scheme:`` declaration in the file. An empty set
    means the theme did not declare one.
    """
    path = theme_file(theme, "theme.css")
    if path is None:
        return set()
    try:
        css = path.read_text()
    except (OSError, ValueError):
        return set()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    m = re.search(r"color-scheme\s*:\s*([^;]+);", css, re.IGNORECASE)
    if not m:
        return set()
    return {tok.lower() for tok in m.group(1).split() if tok.lower() in {"light", "dark"}}


def _theme_mode(theme: str) -> str:
    """Light/dark mode support of a theme, derived from its CSS.

    Returns one of ``"light"``, ``"dark"``, or ``"both"``. Themes without a
    ``color-scheme`` declaration are treated as light-only.
    """
    schemes = _theme_color_schemes(theme)
    if "light" in schemes and "dark" in schemes:
        return "both"
    if "dark" in schemes:
        return "dark"
    return "light"


def _theme_info() -> list[dict[str, str]]:
    """Theme folders on disk, each with its name and supported color mode."""
    return [
        {"name": name, "mode": _theme_mode(name)}
        for name in _theme_folder_names(("theme.css",))
    ]


def _banner_design_names() -> list[str]:
    """Available banner designs: theme folders with artwork and/or styles."""
    return _theme_folder_names(("banner.css", "banner.svg", "banner.html"))


def _transition_names() -> list[str]:
    """Available page-transition designs: theme folders with transition.css."""
    return _theme_folder_names(("transition.css",))


def _theme_folder_names(required: tuple[str, ...]) -> list[str]:
    """Sorted union of theme folder names across THEME_DIRS containing any
    of the required files."""
    return sorted(
        name
        for root in THEME_DIRS
        if root.is_dir()
        for name in {d.name: d for d in root.iterdir() if d.is_dir()}
        if any(theme_file(name, f) for f in required)
    )


def _user_fonts() -> list[dict]:
    """User font folders on disk: FONT_DIRS/{name}/ with a font.css.

    The label and serif flag are read from the font.css ``--font-{name}``
    stack variable; the stylesheet itself is linked on every page (see
    _layout) so the variable and @font-face rules just exist.
    """
    fonts = []
    for name in _font_folder_names():
        path = font_file(name, "font.css")
        css = path.read_text(errors="replace") if path else ""
        m = re.search(
            rf"--font-{re.escape(name)}\s*:\s*([^;]+);",
            re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL),
        )
        stack = m.group(1) if m else ""
        family = re.search(r'"([^"]+)"|\'([^\']+)\'', stack)
        fonts.append(
            {
                "name": name,
                "label": (family.group(1) or family.group(2)) if family else name,
                "serif": bool(re.search(r"(^|,\s*)serif\s*$", stack)),
            }
        )
    return fonts


def _font_folder_names() -> list[str]:
    """Sorted union of font folder names across FONT_DIRS with a font.css."""
    return sorted(
        d.name
        for root in FONT_DIRS
        if root.is_dir()
        for d in root.iterdir()
        if d.is_dir() and (d / "font.css").is_file()
    )


def _valid_name(name: str) -> bool:
    """Guard against path traversal in theme/design names."""
    return bool(name) and "/" not in name and not name.startswith(".")


def theme_file(name: str, filename: str) -> Path | None:
    """Resolve a theme file across THEME_DIRS; the first root with the file
    wins, so users can override or extend built-in themes file by file."""
    return _user_file(THEME_DIRS, name, filename)


def font_file(name: str, filename: str) -> Path | None:
    """Resolve a user font file across FONT_DIRS (first match wins)."""
    return _user_file(FONT_DIRS, name, filename)


def _user_file(dirs: list[Path], name: str, filename: str) -> Path | None:
    if not _valid_name(name) or not _valid_name(filename):
        return None
    for root in dirs:
        path = root / name / filename
        if path.is_file():
            return path
    return None


def _base_css_url(vite_url: str | None) -> str | None:
    """URL for the base stylesheet (None in dev: Vite injects it from JS,
    avoiding the HMR-wrapped module output)."""
    if vite_url:
        return None
    manifest = _manifest()
    if _BASE_CSS_KEY in manifest:
        return f"/{manifest[_BASE_CSS_KEY]['file']}"
    return None


def _theme_css_url(theme: str) -> str | None:
    """URL for the theme stylesheet, served by the backend (dev and prod)."""
    if theme and theme_file(theme, "theme.css"):
        return f"/_themes/{theme}/theme.css"
    return None


def _banner_css_url(design: str) -> str | None:
    """URL for a banner design's stylesheet, served by the backend."""
    if design and theme_file(design, "banner.css"):
        return f"/_themes/{design}/banner.css"
    return None


def _transition_css_url(transition: str) -> str | None:
    """URL for a page-transition stylesheet, served by the backend."""
    if transition and theme_file(transition, "transition.css"):
        return f"/_themes/{transition}/transition.css"
    return None


def _editor_css_url(vite_url: str | None) -> str | None:
    """URL for the editor-specific stylesheet (Vue component styles).

    This is linked by the public-page edit pen so the editor styles are
    loaded before the editor JS dynamic-import resolves.
    """
    if vite_url:
        return None
    manifest = _manifest()
    entry = manifest["src/main.js"]
    base = manifest.get(_BASE_CSS_KEY, {}).get("file")
    for css in entry.get("css", []):
        if css != base:
            return f"/{css}"
    return None


def _inline_asset(url: str) -> str:
    """Read a served asset's content for inlining into the page (prod only).

    Handles build assets (``/_assets/...`` from the Vite build) and theme
    or user-font files (``/_themes/{name}/...``, ``/_fonts/{name}/...``,
    resolved across THEME_DIRS / FONT_DIRS).
    """
    for prefix, resolver in (("/_themes/", theme_file), ("/_fonts/", font_file)):
        if url.startswith(prefix):
            name, _, file = url.removeprefix(prefix).partition("/")
            path = resolver(name, file)
            if path is None:
                raise ValueError(f"not a theme/font asset: {url}")
            return path.read_text()
    return (BUILD / url.lstrip("/")).read_text()


def _inline_script(url: str) -> str:
    """Read a built JS bundle for inlining (prod only).

    Inline modules resolve relative imports against the document URL, not
    the bundle's directory, so rewrite the build's relative chunk
    specifiers ("./chunk.js") to absolute /_assets/ paths.
    """
    js = _inline_asset(url)
    for chunk in _manifest().values():
        file = chunk.get("file", "")
        if file.endswith(".js"):
            js = js.replace(f'"./{file.rsplit("/", 1)[-1]}"', f'"/{file}"')
    return js


def _layout(
    modules: list[str] = (),
    stylesheets: list[str] = (),
    custom_css: str = "",
    theme: str = "",
    banner_design: str = "",
    transition: str = "cube",
    favicon: str = "",
    social: dict[str, str] | None = None,
    lang: str = i18n.ORIGINAL_LANGUAGE,
    canonical: str = "",
    alternates: list[tuple[str, str]] = (),
) -> Template:
    """Page layout template with standard assets and ES-module scripts.

    In dev (PAGERITE_VITE_URL set) assets are linked from the Vite dev
    server and stylesheets use ``blocking="render"`` so the browser waits
    for them before showing the page, avoiding a flash of unstyled content.
    In production all page assets are inlined into the document: stylesheets
    become ``<style>`` elements and module scripts inline ``<script>``s, so
    a page loads with no asset round trips. The on-demand bundles (editor,
    analytics) stay external in both modes.

    Order matters and is fixed: base (Vite build, absent in dev where Vite
    injects it from JS), user fonts (from FONT_DIRS, so themes and custom
    CSS can reference their --font-* variables), theme, banner design and
    page transition (from THEME_DIRS),
    entry-specific stylesheets (e.g. overlayscrollbars.css), then the user's
    custom CSS last so it always wins.

    In dev, pagerite.js re-appends the backend-rendered theme/design links
    (and the custom CSS) after the Vite-injected base styles, keeping this
    order intact.

    ``social`` maps meta keys to contents: ``og:*``/``article:*`` go out as
    property attributes, everything else (description, twitter:*) as name.

    ``lang`` is the served language for <html lang>; an RTL language (ar,
    fa, ...) also puts dir="rtl" on <html> (the editor panel carries its own
    lang="en" dir="ltr", so it is unaffected). ``canonical`` and
    ``alternates`` ((hreflang, href) pairs) are the page's language URLs
    (see docs/localization.md), emitted right after the viewport and before
    the social tags: canonical first, then the hreflang alternates.
    """
    doc = Document(E.Title, lang=lang, dir="rtl" if lang in i18n.RTL_LANGUAGES else "ltr")
    # Responsive layout (see the 48rem breakpoint in pagerite.css) needs
    # the real device width, not the default 980px layout viewport.
    doc.meta(name="viewport", content="width=device-width, initial-scale=1")
    if canonical:
        doc.link(rel="canonical", href=canonical)
    for hreflang, href in alternates:
        doc.link(rel="alternate", hreflang=hreflang, href=href)
    for key, value in (social or {}).items():
        if value:
            if key.startswith(("og:", "article:")):
                doc.meta(property=key, content=value)
            else:
                doc.meta(name=key, content=value)
    # A custom favicon (from the site editor) is linked explicitly; without
    # one, browsers fall back to the build's /favicon.ico by convention.
    if favicon:
        doc.link(rel="icon", href=f"/_f/{favicon}", id="pagerite-favicon")
    # Asset URLs for the on-demand bundles (editor, analytics) for
    # pagerite.js, which injects the 🖊️ edit pens itself once it has
    # validated the session (pages render identically for everyone; editing
    # is gated by the auth proxy in front of /_api). Dev passes the Vite
    # dev-server URLs as meta tags (Vite serves the modules and injects
    # their CSS for hot reloads); production inlines all page assets and
    # carries the on-demand URLs in one JSON script instead.
    vite_url = os.environ.get("PAGERITE_VITE_URL")
    editor_scripts, editor_css = _editor_assets()
    config = {
        "pagerite:editor-src": editor_scripts[-1],
        "pagerite:analytics-src": _analytics_assets()[0][0],
    }
    if editor_css:
        config["pagerite:editor-css"] = editor_css
    if vite_url:
        for key, value in config.items():
            doc.meta(name=key, content=value)
    else:
        # Inert JSON script; URLs never contain "</", but stay safe.
        doc.script(
            HTML(json.dumps(config).replace("</", "<\\/")),
            type="application/json",
            id="pagerite-assets",
        )
    # Stylesheets carry stable ids so the fetch-navigation and the site
    # editor's hot swap can sync <head> positionally (see swapdoc.js).
    # Production inlines the CSS as <style> elements: one less round trip
    # per sheet, and fetch-navigation can carry them across swaps whole.
    sheets = [
        ("pagerite-base", _base_css_url(vite_url)),
        *[
            (f"pagerite-font-{name}", f"/_fonts/{name}/font.css")
            for name in _font_folder_names()
        ],
        ("pagerite-theme", _theme_css_url(theme)),
        ("pagerite-banner", _banner_css_url(banner_design)),
        ("pagerite-transition", _transition_css_url(transition)),
    ]
    for id_, url in sheets:
        if not url:
            continue
        if vite_url:
            doc.link(rel="stylesheet", href=url, blocking="render", id=id_)
        else:
            doc.style(HTML(_inline_asset(url)), id=id_)
    for url in stylesheets:
        if vite_url:
            doc.link(rel="stylesheet", href=url, blocking="render")
        else:
            # Id from the file stem minus the content hash, so the head
            # sync can match sheets across pages (e.g. the analytics sheet
            # exists on /_a only and is added/removed on swaps).
            stem = url.rsplit("/", 1)[-1].removesuffix(".css")
            name = re.sub(r"-[A-Za-z0-9_-]{8}$", "", stem)
            doc.style(HTML(_inline_asset(url)), id=f"pagerite-css-{name}")
    for src in modules:
        if vite_url:
            doc.script(src=src, type="module")
    if custom_css.strip():
        doc.style(custom_css, id="pagerite-user")
    body = (
        doc
        .header(
            E.div(E.Banner, id="page-banner"),
            E.Brand,
            E.nav(E.Nav, id="nav"),
            id="banner",
        )
        .div(
            E.Sidebar,
            E.main(E.Main, id="main"),
            id="content",
        )
        .footer(None)  # kept empty for now; zero-height (see pagerite.css)
    )
    if not vite_url:
        # Inline the bundles at the end of the body: module scripts are
        # deferred anyway, and the page can render before they execute.
        # Escape "</script" so it cannot terminate the element early (only
        # ever occurs inside string literals, where the backslash escape is
        # a no-op).
        for src in modules:
            js = re.sub(r"</script", r"<\\/script", _inline_script(src), flags=re.I)
            # Stable id from the file stem minus the content hash; the
            # analytics page's script (pagerite-js-analytics) is found and
            # re-created by pagerite.js on fetch-navigations to /_a.
            stem = src.rsplit("/", 1)[-1].removesuffix(".js")
            name = re.sub(r"-[A-Za-z0-9_-]{8}$", "", stem)
            body.script(HTML(js), type="module", id=f"pagerite-js-{name}")
    return Template(body)


def _brand_link(brand: str, brand_html: str = "", link_lang: str = "") -> HTML:
    """Header brand: custom HTML (in a #brand wrapper, rendered instead of
    the link) when configured, else the plain brand link; omitted entirely
    when neither is set."""
    if brand_html.strip():
        return HTML(str(E.div(HTML(brand_html), id="brand")))
    return HTML(str(E.a(brand, href=_href("", link_lang), id="brand"))) if brand else HTML("")


def _title(slug: str, node: Node, translation: Translation | None = None, path: str = "") -> str:
    """Menu label: the configured title, prettified slug, "Home" fallback.

    With a translation, its title map (keyed by node path) wins, falling
    back per node to the original English title.
    """
    if translation and (t := translation.titles.get(path)):
        return t
    return node.title or prettify(slug) or "Home"


def _href(path: str, link_lang: str = "") -> str:
    """Site-chrome link to a page: when the page was requested with a
    ?lang= override the query is replicated onto the navigation links it
    renders, so clicks and prefetches (which take the href as-is) stay in
    the chosen language — even without JS (docs/localization.md)."""
    return f"/{path}?lang={link_lang}" if link_lang else f"/{path}"


def _nav_link(
    doc, menu: dict[str, Node], node: Node, path: str, current: str,
    ancestors_current: bool = True, translation: Translation | None = None,
    link_lang: str = "",
) -> None:
    """Render one <li> linking the node. Category labels (no content of
    their own — chunks None, or an empty page as left by the site editor's
    page creation) link straight to their first child page, so normal
    navigation bypasses the placeholder/empty page at their own URL."""
    # The navbar highlights a top-level item also when viewing any of its
    # subpages; the sidebar highlights only the actually viewed page.
    is_current = current == path or (
        ancestors_current and path and current.startswith(f"{path}/")
    )
    href = _href(path, link_lang)
    if not node.chunks and (leaf := first_leaf(menu, path)) is not None:
        href = _href(leaf, link_lang)
    doc.li.a(
        _title(path.rpartition("/")[2], node, translation, path),
        href=href,
        **{"class": "current"} if is_current else {},
    )


def nav_html(menu: dict[str, Node], current: str, translation: Translation | None = None, link_lang: str = "") -> HTML:
    """Render the contents of the #nav element for the current path.

    Top-level items in menu order; the front page (slug "", href "/")
    competes by its order key like any sibling. Subitems of the current
    section go to the sidebar (sidebar_html).
    """
    nav = E.ul
    with nav:
        for slug, node in sorted_nodes(menu):
            if node.published:
                _nav_link(nav, menu, node, slug, current, translation=translation, link_lang=link_lang)
    return HTML(str(nav))


def sidebar_html(menu: dict[str, Node], current: str, translation: Translation | None = None, link_lang: str = "") -> HTML:
    """Render the #sidebar element for the current path (empty when none).

    The sidebar is the current main level section's sub-navigation: the
    section's direct children as the top list level, with each item's own
    published children nested under it (third level and deeper), so it
    exists only when there is something to navigate. It renders only from
    the second level down: main-level pages (and the front page) list
    their children as cards after the content instead of a sidebar. From
    there, the section must offer at least two published items, or exactly
    one while viewing anything else than that only page — the section
    index, a 404, a grandchild (otherwise those pages offer no way to
    reach the child) — and viewing that only page itself still shows the
    sidebar when the page has published children of its own to reach.
    Leaf pages, the sole childless page of a one-page section and
    childless sections get no aside element at all (rather than an empty
    or useless one-item box).
    """
    if not current or "/" not in current:
        return HTML("")
    section = current.split("/", 1)[0]
    node = menu.get(section)
    if node is None:
        return HTML("")
    items = [(s, c) for s, c in sorted_nodes(node.children) if c.published]
    if not items:
        return HTML("")
    if len(items) == 1 and current == f"{section}/{items[0][0]}":
        # Viewing the only item: useless unless it has children to reach.
        if not any(c.published for c in items[0][1].children.values()):
            return HTML("")
    nav = E.ul
    with nav:
        for slug, child in items:
            _sidebar_item(nav, menu, child, f"{section}/{slug}", current, translation, link_lang)
    return HTML(str(E.aside(nav, id="sidebar")))


def _sidebar_item(doc, menu: dict[str, Node], node: Node, path: str, current: str, translation: Translation | None = None, link_lang: str = "") -> None:
    """One sidebar <li>: the node link, with its published children as a
    nested list (third level and deeper, recursively)."""
    _nav_link(doc, menu, node, path, current, ancestors_current=False, translation=translation, link_lang=link_lang)
    sub = [(s, c) for s, c in sorted_nodes(node.children) if c.published]
    if sub:
        # doc.li.a(...) above left the <li> open for nesting.
        with doc.ul:
            for slug, child in sub:
                _sidebar_item(doc, menu, child, f"{path}/{slug}", current, translation, link_lang)


def first_leaf(menu: dict[str, Node], path: str) -> str | None:
    """First published descendant page (chunks set) in menu order.

    This is the nav-link target for content-less category labels.
    """
    chain = resolve(menu, path)
    if chain is None:
        return None
    return _first_leaf(chain[-1], path)


def _first_leaf(node: Node, path: str) -> str | None:
    for slug, child in sorted_nodes(node.children):
        if not child.published:
            continue
        cpath = f"{path}/{slug}" if path else slug
        if child.chunks:
            return cpath
        if (leaf := _first_leaf(child, cpath)) is not None:
            return leaf
    return None


def banner_html(menu: dict[str, Node], path: str, theme: str = "") -> HTML:
    """Resolve the banner for a path: the effective banner design's inline
    SVG artwork first, then the user's own banner HTML — always last, so
    author code (e.g. <style> overrides) wins over the design's own styles.

    The design comes from banner_design(); the user banner from the nearest
    node on the ancestor chain (the node itself first), then the front page.
    The front page is a top-level *sibling* of the other main-level nodes,
    not their parent, so it never appears in the chain and is consulted
    explicitly, last. The snippet is raw trusted HTML, so a banner can be
    anything — an img, a styled div, canvas + script.
    """
    parts = []
    design = banner_design(menu, path, theme)
    if design:
        parts.append(_design_banner(design))
    source = banner_source(menu, path)
    if source is not None:
        parts.append(HTML(resolve(menu, source)[-1].banner))
    return HTML("".join(str(p) for p in parts))


def _design_banner(design: str) -> HTML:
    """The design's inline banner artwork (empty for none/unknown designs).

    banner.html (arbitrary markup: canvas + style + script...) takes
    precedence over banner.svg. Read from disk on every request: design
    files are never built/cached, so editing them on disk shows on the
    next page load, even in prod. The data-design wrapper marks the
    artwork as the design's (not author code), so the site editor's live
    banner preview keeps it in place.
    """
    if not _valid_name(design):
        return HTML("")
    path = theme_file(design, "banner.html") or theme_file(design, "banner.svg")
    if path is None:
        return HTML("")
    body = path.read_text()
    return HTML(f'<div data-design="{design}">{body}</div>')


def theme_banner_design(theme: str) -> str:
    """The theme's own banner design (a theme folder doubles as a banner
    design when it ships banner.css, banner.svg or banner.html), "" if it
    has none."""
    if any(theme_file(theme, f) for f in ("banner.css", "banner.svg", "banner.html")):
        return theme
    return ""


def banner_design(menu: dict[str, Node], path: str, theme: str = "") -> str:
    """The effective banner design name at ``path`` ("" = no design).

    Nodes set ``banner_design`` to a design name, "" (explicitly none) or
    None (inherit). Resolution walks the ancestor chain from the node
    upwards, then the front page, then falls back to the active theme's own
    design.
    """
    chain = resolve(menu, path) or []
    for node in reversed(chain):
        if node.banner_design is not None:
            return node.banner_design
    front = menu.get("")
    if front and front.banner_design is not None:
        return front.banner_design
    return theme_banner_design(theme)


def banner_design_source(
    menu: dict[str, Node], path: str, theme: str = ""
) -> str | None:
    """Which node's banner-design setting *would* apply at ``path`` if the
    node itself set nothing: the nearest ancestor's path ("" = the front
    page), or None when the active theme's default applies. Used by the
    banner editor for the design selector's inherit label."""
    chain = resolve(menu, path) or []
    segs = path.split("/")
    # Skip the node itself (chain[-1]): its own setting is not inheritance.
    for i in range(len(chain) - 2, -1, -1):
        if chain[i].banner_design is not None:
            return "/".join(segs[: i + 1])
    # The front page is a top-level sibling of the chain, not an ancestor;
    # it does not inherit from itself.
    front = menu.get("")
    if path and front and front.banner_design is not None:
        return ""
    return None


def banner_source(menu: dict[str, Node], path: str) -> str | None:
    """Which node's banner HTML applies at ``path``: the nearest ancestor with
    one set (the front page, a top-level sibling of the chain, last). None =
    no user banner anywhere (only the design artwork renders, if any).
    Whitespace-only banners count as empty: clearing the editor can leave a
    stray newline behind."""
    chain = resolve(menu, path) or []
    segs = path.split("/")
    for i in range(len(chain) - 1, -1, -1):
        if chain[i].banner.strip():
            return "/".join(segs[: i + 1])
    front = menu.get("")
    if front and front.banner.strip():
        return ""
    return None


def page_content(menu: dict[str, Node], data: Data, path: str, translation: Translation | None = None, link_lang: str = "", lang: str = "") -> HTML:
    """Render the contents of the #main element for a page.

    A page with published children (a category page) lists them as cards
    after the markdown content. With a translation, its Markdown goes
    through the same render pipeline; missing pieces (markdown=None, absent
    title entries) fall back to the original. ``lang`` feeds the cards'
    per-target localization.
    """
    node = resolve(menu, path)[-1]
    content = node_markdown(data, node) or ""
    title = node.title
    if translation:
        if translation.markdown is not None:
            content = translation.markdown
        title = _title(path.rpartition("/")[2], node, translation, path) if node.title else title
    # The title is injected into the markdown (as # title when it has no
    # h1 of its own), so title and content render as one article.
    rendered = render(content, path, node.created, node.modified, title=title)
    # Long articles get .multicol: the article column cap lifts (see the
    # #content grid in pagerite.css) and the .cols segments lay out in at
    # most two columns. The html is already segmented by render() — the
    # whole layout is driven by these classes.
    doc = E.article(class_="multicol") if rendered.multicol else E.article
    with doc:
        doc(HTML(rendered.html))
        _cards(doc, menu, data, node, path, translation, link_lang, lang)
    return HTML(str(doc))


def _cards(doc, menu: dict[str, Node], data: Data, node: Node, path: str, translation: Translation | None = None, link_lang: str = "", lang: str = "") -> None:
    """Card stacks of the node's published children (nothing when childless).

    One column per direct child, all in a single full-width row (the .wide
    breakout): the columns grow to fill the page and shrink rather than
    wrap. A column holds the child's whole subtree flattened in menu order
    — nesting levels are not split out — starting with the first page that
    has actual content (the child itself when it does, its first leaf
    otherwise, recursively). Each card is one <a> showing the page's share
    image (the same heuristics as og:image) as the cover and its title;
    image-less cards get a gradient cover and also show the description.
    Only phrasing-level elements (spans) go inside the <a>: as a formatting
    element it would be cloned by the HTML parser around any block-level
    child, splitting one card into several links.
    """
    items = [(s, c) for s, c in sorted_nodes(node.children) if c.published]
    if not items:
        return
    with doc.div(class_="cards wide"):
        for slug, child in items:
            cpath = f"{path}/{slug}" if path else slug
            entries = list(_walk(child, cpath))
            if not entries:
                continue
            with doc.div(class_="stack"):
                for epath, enode in entries:
                    _card(doc, data, enode, epath, translation, link_lang, lang)


def _walk(node: Node, path: str):
    """Published content pages of a subtree, pre-order in menu order: the
    node itself first when it has content (the stack's landing card), then
    its descendants (content-less nodes contribute only their subtree)."""
    if node.chunks:
        yield path, node
    for slug, child in sorted_nodes(node.children):
        if child.published:
            yield from _walk(child, f"{path}/{slug}")


def _card(doc, data: Data, node: Node, path: str, translation: Translation | None = None, link_lang: str = "", lang: str = "") -> None:
    """One card in a stack: cover + title, plus the description when the
    page has no image (its card shows a gradient cover instead).

    The card text localizes per target article where that page is
    available in the language: the title comes from the translation's
    title map and the cover/description heuristics run on the target's
    hybrid Markdown — with per-card fallback to the original otherwise.
    """
    image = description = ""
    if node.chunks:
        md = node_markdown(data, node) or ""
        if lang and lang in node.langs:
            md = i18n.hybrid_markdown(data, node, path, lang)
        html = render(md, path, node.created, node.modified).html
        image, _ = _media(html)
        if not image:
            description = _description(html, 150)
    with doc.a(href=_href(path, link_lang), class_="card"):
        if image:
            doc.span(class_="cover", style=f'background-image: url("{image}")')
        else:
            doc.span(class_="cover")
        doc.span(_title(path.rpartition("/")[2], node, translation, path), class_="title")
        if description:
            doc.span(description, class_="desc")


_FIRST_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TAG = re.compile(r"<[^>]+>")
_IMG_TAG = re.compile(r"<img\b[^>]*>")
_VIDEO_TAG = re.compile(r"<video\b[^>]*>")
_ATTR_SRC = re.compile(r'src="([^"]+)"')
_ATTR_CLASS = re.compile(r'class="([^"]*)"')


_SENTENCE_END = re.compile(r"[.!?][”'\")]*(?=\s|$)")


def _description(html: str, limit: int = 200) -> str:
    """Article description: the first paragraph's text, truncated at a
    sentence end (or, failing that, a word boundary) within ``limit``.
    Used for og:description."""
    m = _FIRST_P.search(html)
    text = unescape(_TAG.sub("", m.group(1) if m else ""))
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    # Prefer a clean cut: the last sentence ending within the limit, as
    # long as it does not reduce the description to a tiny fragment.
    if (end := max((m.end() for m in _SENTENCE_END.finditer(text[:limit])), default=0)) > limit // 2:
        return text[:end]
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _media(html: str) -> tuple[str, str]:
    """Raw (image, video) srcs from rendered article HTML (relative ok).

    Image preference: an image with class "hero" (author override, may
    appear anywhere in the article), then the first raster image (SVGs
    rasterize poorly or not at all on many social scrapers), then the
    first SVG. Video: the first <video> — og:video is in the OGP spec and
    honored mainly by Facebook; X/Twitter ignores it.
    """
    hero = raster = svg = video = ""
    for tag in _IMG_TAG.findall(html):
        if not (src := _ATTR_SRC.search(tag)):
            continue
        src = src.group(1)
        cls = _ATTR_CLASS.search(tag)
        if cls and "hero" in cls.group(1).split():
            hero = src
            break
        if src.lower().split("?")[0].endswith(".svg"):
            svg = svg or src
        else:
            raster = raster or src
            # Keep scanning: a later hero still wins.
    for tag in _VIDEO_TAG.findall(html):
        if m := _ATTR_SRC.search(tag):
            video = m.group(1)
            break
    return hero or raster or svg, video


def _share_media(html: str, base_url: str) -> tuple[str, str]:
    """(image, video) share URLs from the rendered article.

    The _media picks as absolute URLs built from the request base —
    social scrapers cannot use relative ones. Extension-less store links
    (``/_f/<hash>``) are used as-is: the server negotiates the format
    from the scraper's Accept header (no explicit image/avif|webp → JPEG,
    which every scraper supports).
    """
    if not base_url:
        return "", ""

    def absolute(src: str) -> str:
        src = unescape(src)
        return src if src.startswith(("http://", "https://")) else f"{base_url}{src}"

    image, video = _media(html)
    return (absolute(image) if image else "", absolute(video) if video else "")


def _social_meta(
    node: Node, path: str, title: str, html: str, brand: str, base_url: str,
) -> dict[str, str]:
    """Open Graph/Twitter/SEO meta tags for a content page.

    Heuristics over the rendered article: the description is the first
    paragraph's text (truncated at ~200 chars on a word boundary), the
    share image the article's first <img> — authors lead with their most
    representative figure. Absolute URLs are built from the request's base
    (social scrapers cannot use relative ones).

    ``twitter:image`` pins extension-less store links to the ``.webp``
    variant: X only honors WebP via twitter:image (not og:image) and its
    scraper cannot be trusted to negotiate via Accept.
    """
    url = f"{base_url}/{path}" if base_url else ""
    text = _description(html)
    image, video = _share_media(html, base_url)
    twitter_image = (
        re.sub(r"(/_f/[0-9a-f]{12})$", r"\1.webp", image) if image else ""
    )
    return {
        "description": text,
        "og:type": "article",
        "og:title": title,
        "og:description": text,
        "og:url": url,
        "og:site_name": brand,
        "og:image": image,
        "og:video": video,
        "article:published_time": node.created.isoformat(),
        "article:modified_time": node.modified.isoformat(),
        "twitter:card": "summary_large_image" if image else "summary",
        "twitter:image": twitter_image,
    }


def render_page(
    menu: dict[str, Node],
    data: Data,
    path: str,
    brand: str = SITE_NAME,
    custom_css: str = "",
    theme: str = "",
    favicon: str = "",
    brand_html: str = "",
    base_url: str = "",
    transition: str = "cube",
    lang: str = i18n.ORIGINAL_LANGUAGE,
    translation: Translation | None = None,
    link_lang: str = "",
) -> str:
    """Render a full HTML page for the slug path.

    ``lang``/``translation`` serve a translated version (see
    docs/localization.md): None translation = the English original.
    ``link_lang`` is the ?lang= override the page was requested with,
    replicated onto the navigation links so the language sticks.
    """
    node = resolve(menu, path)[-1]
    original = i18n.primary_lang(menu, path)
    if translation is None:
        lang = original
    title = _title(path.rpartition("/")[2], node, translation, path)
    main = page_content(menu, data, path, translation, link_lang, lang)
    social = _social_meta(node, path, title, str(main), brand, base_url)
    # Canonical/hreflang URLs (docs/localization.md): the canonical names
    # the actually served language — the plain URL for the original (for
    # SEO the non-query URL means the article's language), ?lang= for a
    # translation — regardless of how the language was arrived at (query
    # or header). The alternates are site-wide, the same set on every
    # page: the configured translate_langs (the translator works to fill
    # them all in), x-default first (the plain, autodetecting URL), then
    # every language explicitly, the page's own primary included.
    canonical = ""
    alternates = []
    if base_url:
        url = f"{base_url}/{path}"
        canonical = url if lang == original else f"{url}?lang={lang}"
        if data.translate_langs:
            alternates = [("x-default", url)] + [
                (tag, f"{url}?lang={tag}")
                for tag in sorted({original, *data.translate_langs})
            ]
    return str(
        _layout(
            *_page_assets(), custom_css, theme, banner_design(menu, path, theme),
            transition, favicon, social, lang, canonical, alternates,
        )(
            Title=f"{title} – {brand}" if brand else title,
            Brand=_brand_link(brand, brand_html, link_lang),
            Nav=nav_html(menu, path, translation, link_lang),
            Sidebar=sidebar_html(menu, path, translation, link_lang),
            Banner=banner_html(menu, path, theme),
            Main=main,
        ),
    )


def render_category(
    menu: dict[str, Node],
    data: Data,
    path: str,
    brand: str = SITE_NAME,
    custom_css: str = "",
    theme: str = "",
    favicon: str = "",
    brand_html: str = "",
    transition: str = "cube",
    lang: str = i18n.ORIGINAL_LANGUAGE,
    translation: Translation | None = None,
    link_lang: str = "",
) -> str:
    """Render the listing for a content-less category label (404).

    The node exists in the tree but has no page of its own: its published
    children are listed as cards, like on a category page with content.
    Nav links point straight at the first child, so this is mainly seen
    in the site editor, where the pen creates the landing page.

    With a translation (titles only — the category has no Markdown) the
    heading, navigation and card text localize per target article
    (docs/localization.md); ``link_lang`` replicates the ?lang= override
    onto the navigation links as on content pages.
    """
    node = resolve(menu, path)[-1]
    if translation is None:
        lang = i18n.primary_lang(menu, path)
    title = _title(path.rpartition("/")[2], node, translation, path)
    doc = E.article
    with doc:
        doc.h1(title)
        if any(c.published for c in node.children.values()):
            _cards(doc, menu, data, node, path, translation, link_lang, lang)
        else:
            doc.p("This section has no page of its own yet.")
    return str(
        _layout(*_page_assets(), custom_css, theme, banner_design(menu, path, theme), transition, favicon, lang=lang)(
            Title=f"{title} – {brand}" if brand else title,
            Brand=_brand_link(brand, brand_html, link_lang),
            Nav=nav_html(menu, path, translation, link_lang),
            Sidebar=sidebar_html(menu, path, translation, link_lang),
            Banner=banner_html(menu, path, theme),
            Main=HTML(str(doc)),
        ),
    )


def render_not_found(
    menu: dict[str, Node],
    path: str,
    brand: str = SITE_NAME,
    custom_css: str = "",
    theme: str = "",
    favicon: str = "",
    brand_html: str = "",
    transition: str = "cube",
) -> str:
    """Render a 404 page within the normal layout."""
    doc = E.article
    with doc:
        doc.h1("Not Found")
        doc.p(f"No article at /{path}. If there was before, it may have been deleted.")
    return str(
        _layout(*_page_assets(), custom_css, theme, banner_design(menu, path, theme), transition, favicon)(
            Title=f"Not Found – {brand}" if brand else "Not Found",
            Brand=_brand_link(brand, brand_html),
            Nav=nav_html(menu, path),
            Sidebar=sidebar_html(menu, path),
            Banner=banner_html(menu, path, theme),
            Main=HTML(str(doc)),
        ),
    )


def _page_assets() -> tuple[list[str], list[str]]:
    """Script and stylesheet URLs for public pages (pagerite entry).

    Dev mode loads the entry from the Vite dev server; production uses
    the Vite build manifest to resolve the hashed asset names. CSS imported
    by the entry (e.g. overlayscrollbars.css) is extracted by Vite and must
    be linked separately.
    """
    vite_url = os.environ.get("PAGERITE_VITE_URL")
    if vite_url:
        return [f"{vite_url}/src/pagerite.js"], []
    if "page" not in _asset_cache:
        manifest = _manifest()
        entry = manifest["src/pagerite.js"]
        scripts = [f"/{entry['file']}"]
        stylesheets = [f"/{css}" for css in entry.get("css", [])]
        _asset_cache["page"] = scripts, stylesheets
    return _asset_cache["page"]


def _editor_assets() -> tuple[list[str], str | None]:
    """Script URL and editor-specific CSS URL for the public-page edit pen.

    The shared CSS is already linked on the page, so the pen only needs the
    editor-specific stylesheet.
    """
    vite_url = os.environ.get("PAGERITE_VITE_URL")
    if vite_url:
        return [f"{vite_url}/@vite/client", f"{vite_url}/src/main.js"], None
    if "editor" not in _asset_cache:
        manifest = _manifest()
        entry = manifest["src/main.js"]
        _asset_cache["editor"] = [f"/{entry['file']}"], _editor_css_url(None)
    return _asset_cache["editor"]


def _analytics_assets() -> tuple[list[str], list[str]]:
    """Script and stylesheet URLs for the analytics page entry."""
    vite_url = os.environ.get("PAGERITE_VITE_URL")
    if vite_url:
        return [f"{vite_url}/src/analytics-main.js"], []
    if "analytics" not in _asset_cache:
        manifest = _manifest()
        entry = manifest["src/analytics-main.js"]
        scripts = [f"/{entry['file']}"]
        stylesheets = [f"/{css}" for css in entry.get("css", [])]
        _asset_cache["analytics"] = scripts, stylesheets
    return _asset_cache["analytics"]


def render_analytics(
    menu: dict[str, Node],
    brand: str = SITE_NAME,
    custom_css: str = "",
    theme: str = "",
    favicon: str = "",
    brand_html: str = "",
    transition: str = "cube",
) -> str:
    """Render the analytics viewer as a normal page at /_a.

    The analytics entry is inlined into this page only (prod) or loaded
    from the Vite dev server (dev); its stylesheet rides along in <head>
    so fetch-navigations can sync it into the live document. The initial
    range is not rendered in: the client takes it from the URL hash or
    derives it from the analytics data itself.
    """
    page_scripts, page_stylesheets = _page_assets()
    analytics_scripts, analytics_stylesheets = _analytics_assets()
    scripts = page_scripts + analytics_scripts
    stylesheets = page_stylesheets + analytics_stylesheets
    doc = E.article
    with doc:
        # .wide: the dashboard breaks out of the article column to the full
        # viewport width, like wide figures (see the .wide rules).
        doc.div(id="analytics-app", class_="wide")
    return str(
        _layout(
            scripts,
            stylesheets,
            custom_css,
            theme,
            banner_design(menu, "_a", theme),
            transition,
            favicon,
        )(
            Title=f"Analytics – {brand}" if brand else "Analytics",
            Brand=_brand_link(brand, brand_html),
            Nav=nav_html(menu, "_a"),
            Sidebar=sidebar_html(menu, "_a"),
            Banner=banner_html(menu, "_a", theme),
            Main=HTML(str(doc)),
        ),
    )
