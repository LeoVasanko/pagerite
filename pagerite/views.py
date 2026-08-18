"""HTML rendering: page layout template, navigation, content pages.

All pages share one static layout, defined once as an html5tagger Template
with placeholders (capitalized attributes) filled per request. The dynamic
regions carry stable ids (#nav, #main) so that the fetch-navigation script
can swap them without reloading the page chrome.

Navigation walks the Node tree directly (see data.py): nav_html lists the
top level — the front page (slug "") is an ordinary top-level item, not
the parent of the others — and sidebar_html the sub-navigation of the
current top-level section, rendered only when the section offers at
least two published items. Nodes without content are category labels; nav links
to them point straight at their first child page (first_leaf), and their
own URL renders a placeholder page (render_category).
"""

from pathlib import Path
import json
import os

from html5tagger import HTML, Document, E, Template

from pagerite.data import Node, prettify, resolve, sorted_nodes
from pagerite.markdown import has_h1, render

SITE_NAME = "Pagerite"
BUILD = Path(__file__).with_name("frontend-build")
THEMES = Path(__file__).parent / "themes"

# The base CSS is built by Vite as a separate entry so the backend can link
# it independently of the theme. Themes and banner designs are plain .css
# files in THEMES/{name}/, served by the backend at /_themes/{name}/... and
# re-read from disk on every request (see app.py), so they are never built.
_BASE_CSS_KEY = "src/assets/pagerite.css"

_manifest_cache: dict | None = None
_asset_cache: dict[str, tuple] = {}


def _manifest() -> dict:
    global _manifest_cache
    if _manifest_cache is None:
        _manifest_cache = json.loads((BUILD / ".vite/manifest.json").read_text())
    return _manifest_cache


def _theme_names() -> list[str]:
    """Theme folders on disk (a folder is a theme when it has theme.css)."""
    return sorted(
        d.name for d in THEMES.iterdir() if d.is_dir() and (d / "theme.css").exists()
    )


def _banner_design_names() -> list[str]:
    """Available banner designs: theme folders with artwork and/or styles."""
    return sorted(
        d.name
        for d in THEMES.iterdir()
        if d.is_dir()
        and any((d / f).exists() for f in ("banner.css", "banner.svg", "banner.html"))
    )


def _valid_name(name: str) -> bool:
    """Guard against path traversal in theme/design names."""
    return bool(name) and "/" not in name and not name.startswith(".")


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
    if theme and _valid_name(theme) and (THEMES / theme / "theme.css").exists():
        return f"/_themes/{theme}/theme.css"
    return None


def _banner_css_url(design: str) -> str | None:
    """URL for a banner design's stylesheet, served by the backend."""
    if design and _valid_name(design) and (THEMES / design / "banner.css").exists():
        return f"/_themes/{design}/banner.css"
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


def _layout(
    modules: list[str] = (),
    custom_css: str = "",
    theme: str = "",
    banner_design: str = "",
    favicon: str = "",
) -> Template:
    """Page layout template with standard asset URLs and ES-module scripts.

    Stylesheets use ``blocking="render"`` so the browser waits for them before
    showing the page, avoiding a flash of unstyled content. Order matters and
    is fixed: base (Vite build, absent in dev where Vite injects it from JS),
    theme and banner design (backend-served from pagerite/themes/), then the
    user's custom CSS last so it always wins.

    In dev, pagerite.js re-appends the backend-rendered theme/design links
    (and the custom CSS) after the Vite-injected base styles, keeping this
    order intact.
    """
    doc = Document(E.Title, lang="en")
    # Responsive layout (see the 48rem breakpoint in pagerite.css) needs
    # the real device width, not the default 980px layout viewport.
    doc.meta(name="viewport", content="width=device-width, initial-scale=1")
    # A custom favicon (from the site editor) is linked explicitly; without
    # one, browsers fall back to the build's /favicon.ico by convention.
    if favicon:
        doc.link(rel="icon", href=f"/_f/{favicon}", id="pagerite-favicon")
    # Editor asset URLs for pagerite.js, which injects the 🖊️ edit pens
    # itself once it has validated the session (pages render identically
    # for everyone; editing is gated by the auth proxy in front of /_api).
    script, editor_css = _editor_assets()
    doc.meta(name="pagerite:editor-src", content=script[-1])
    if editor_css:
        doc.meta(name="pagerite:editor-css", content=editor_css)
    # Stylesheet links carry stable ids so the site editor's hot swap can
    # keep each sheet at its rendered position (see swapRegions).
    vite_url = os.environ.get("PAGERITE_VITE_URL")
    sheets = [
        ("pagerite-base", _base_css_url(vite_url)),
        ("pagerite-theme", _theme_css_url(theme)),
        ("pagerite-banner", _banner_css_url(banner_design)),
    ]
    for id_, url in sheets:
        if url:
            doc.link(rel="stylesheet", href=url, blocking="render", id=id_)
    for src in modules:
        doc.script(src=src, type="module")
    if custom_css.strip():
        doc.style(custom_css, id="pagerite-user")
    return Template(
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
        .footer(None),  # kept empty for now; zero-height (see pagerite.css)
    )


def _brand_link(brand: str, brand_html: str = "") -> HTML:
    """Header brand: custom HTML (in a #brand wrapper, rendered instead of
    the link) when configured, else the plain brand link; omitted entirely
    when neither is set."""
    if brand_html.strip():
        return HTML(str(E.div(HTML(brand_html), id="brand")))
    return HTML(str(E.a(brand, href="/", id="brand"))) if brand else HTML("")


def _title(slug: str, node: Node) -> str:
    """Menu label: the configured title, prettified slug, "Home" fallback."""
    return node.title or prettify(slug) or "Home"


def _nav_link(doc, menu: dict[str, Node], node: Node, path: str, current: str) -> None:
    """Render one <li> linking the node. Category labels (no content of
    their own — None, or empty markdown as left by the site editor's
    page creation) link straight to their first child page, so normal
    navigation bypasses the placeholder/empty page at their own URL."""
    # A top-level item is current also when viewing any of its subpages.
    is_current = current == path or (path and current.startswith(f"{path}/"))
    href = f"/{path}"
    if not node.content and (leaf := first_leaf(menu, path)) is not None:
        href = f"/{leaf}"
    doc.li.a(
        _title(path.rpartition("/")[2], node),
        href=href,
        **{"class": "current"} if is_current else {},
    )


def nav_html(menu: dict[str, Node], current: str) -> HTML:
    """Render the contents of the #nav element for the current path.

    Top-level items in menu order; the front page (slug "", href "/")
    competes by its order key like any sibling. Subitems of the current
    section go to the sidebar (sidebar_html).
    """
    nav = E.ul
    with nav:
        for slug, node in sorted_nodes(menu):
            if node.published:
                _nav_link(nav, menu, node, slug, current)
    return HTML(str(nav))


def sidebar_html(menu: dict[str, Node], current: str) -> HTML:
    """Render the #sidebar element for the current path (empty when none).

    The sidebar is the current main level section's sub-navigation, so it
    exists only when there is something to navigate: the section must
    offer at least two published items, or exactly one while viewing
    anything else than that only page — the section index, a 404, a
    grandchild (otherwise those pages offer no way to reach the child).
    The front page, leaf pages, the sole page of a one-page section and
    childless sections get no aside element at all (rather than an empty
    or useless one-item box).
    """
    if not current:
        return HTML("")
    section = current.split("/", 1)[0]
    node = menu.get(section)
    if node is None:
        return HTML("")
    items = [(s, c) for s, c in sorted_nodes(node.children) if c.published]
    if not items or (len(items) == 1 and current == f"{section}/{items[0][0]}"):
        return HTML("")
    nav = E.ul
    with nav:
        for slug, child in items:
            _nav_link(nav, menu, child, f"{section}/{slug}", current)
    return HTML(str(E.aside(nav, id="sidebar")))


def first_leaf(menu: dict[str, Node], path: str) -> str | None:
    """First published descendant page (content set) in menu order.

    This is the nav-link target for content-less category labels.
    """
    chain = resolve(menu, path)
    if chain is None:
        return None
    return _first_leaf(chain[-1], path)


def _first_leaf(node: Node, path: str) -> str | None:
    for slug, child in sorted_nodes(node.children):
        cpath = f"{path}/{slug}" if path else slug
        if child.published and child.content:
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
    html = THEMES / design / "banner.html"
    svg = THEMES / design / "banner.svg"
    if html.exists():
        body = html.read_text()
    elif svg.exists():
        body = svg.read_text()
    else:
        return HTML("")
    return HTML(f'<div data-design="{design}">{body}</div>')


def banner_design(menu: dict[str, Node], path: str, theme: str = "") -> str:
    """The effective banner design name at ``path`` ("" = no design).

    Nodes set ``banner_design`` to a design name, "" (explicitly none) or
    None (inherit). Resolution walks the ancestor chain from the node
    upwards, then the front page, then falls back to the active theme's own
    design (a theme folder doubles as a banner design when it ships
    banner.css or banner.svg).
    """
    chain = resolve(menu, path) or []
    for node in reversed(chain):
        if node.banner_design is not None:
            return node.banner_design
    front = menu.get("")
    if front and front.banner_design is not None:
        return front.banner_design
    if (
        _valid_name(theme)
        and any(
            (THEMES / theme / f).exists()
            for f in ("banner.css", "banner.svg", "banner.html")
        )
    ):
        return theme
    return ""


def banner_design_source(
    menu: dict[str, Node], path: str, theme: str = ""
) -> str | None:
    """Which node's banner-design setting applies at ``path`` (like
    banner_source), or None when the active theme's default applies.
    Used by the site editor for the design selector's inherit label."""
    chain = resolve(menu, path) or []
    segs = path.split("/")
    for i in range(len(chain) - 1, -1, -1):
        if chain[i].banner_design is not None:
            return "/".join(segs[: i + 1])
    front = menu.get("")
    if front and front.banner_design is not None:
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


def page_content(menu: dict[str, Node], path: str) -> HTML:
    """Render the contents of the #main element for a page."""
    node = resolve(menu, path)[-1]
    doc = E.article
    with doc:
        # An h1 in the markdown owns the article heading; the title is
        # only rendered as h1 when the markdown has none of its own.
        if not has_h1(node.content or ""):
            doc.h1(node.title)
        doc.div(
            HTML(render(node.content or "", path, node.created, node.modified)),
            class_="body",
        )
    return HTML(str(doc))


def render_page(
    menu: dict[str, Node],
    path: str,
    brand: str = SITE_NAME,
    custom_css: str = "",
    theme: str = "",
    favicon: str = "",
    brand_html: str = "",
) -> str:
    """Render a full HTML page for the slug path."""
    node = resolve(menu, path)[-1]
    title = _title(path.rpartition("/")[2], node)
    return str(
        _layout(_page_assets(), custom_css, theme, banner_design(menu, path, theme), favicon)(
            Title=f"{title} – {brand}" if brand else title,
            Brand=_brand_link(brand, brand_html),
            Nav=nav_html(menu, path),
            Sidebar=sidebar_html(menu, path),
            Banner=banner_html(menu, path, theme),
            Main=page_content(menu, path),
        ),
    )


def render_category(
    menu: dict[str, Node],
    path: str,
    brand: str = SITE_NAME,
    custom_css: str = "",
    theme: str = "",
    favicon: str = "",
    brand_html: str = "",
) -> str:
    """Render the placeholder for a content-less category label (404).

    The node exists in the tree but has no page of its own. Nav links
    point straight at its first child, so this is mainly seen in the site
    editor, where the pen creates the landing page.
    """
    node = resolve(menu, path)[-1]
    title = _title(path.rpartition("/")[2], node)
    sidebar = sidebar_html(menu, path)
    doc = E.article
    with doc:
        doc.h1(title)
        if sidebar:
            doc.p("Pages in this section are listed in the menu on the left.")
        else:
            doc.p("This section has no page of its own yet.")
    return str(
        _layout(_page_assets(), custom_css, theme, banner_design(menu, path, theme), favicon)(
            Title=f"{title} – {brand}" if brand else title,
            Brand=_brand_link(brand, brand_html),
            Nav=nav_html(menu, path),
            Sidebar=sidebar,
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
) -> str:
    """Render a 404 page within the normal layout."""
    doc = E.article
    with doc:
        doc.h1("Not Found")
        doc.p(f"No article at /{path}. If there was before, it may have been deleted.")
    return str(
        _layout(_page_assets(), custom_css, theme, banner_design(menu, path, theme), favicon)(
            Title=f"Not Found – {brand}" if brand else "Not Found",
            Brand=_brand_link(brand, brand_html),
            Nav=nav_html(menu, path),
            Sidebar=sidebar_html(menu, path),
            Banner=banner_html(menu, path, theme),
            Main=HTML(str(doc)),
        ),
    )


def _page_assets() -> list[str]:
    """Script URLs for public pages (pagerite entry).

    Dev mode loads the entry from the Vite dev server; production uses
    the Vite build manifest to resolve the hashed asset names.
    """
    vite_url = os.environ.get("PAGERITE_VITE_URL")
    if vite_url:
        return [f"{vite_url}/src/pagerite.js"]
    if "page" not in _asset_cache:
        manifest = _manifest()
        entry = manifest["src/pagerite.js"]
        _asset_cache["page"] = [f"/{entry['file']}"]
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
