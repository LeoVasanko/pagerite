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

# Shared CSS built as separate entries so the backend can link base and theme
# independently. Order matters: base first, theme overrides it.
_BASE_CSS_KEY = "src/assets/pagerite.css"
_THEME_CSS_KEY = "src/assets/themes/{theme}/theme.css"

_manifest_cache: dict | None = None
_asset_cache: dict[str, tuple] = {}


def _manifest() -> dict:
    global _manifest_cache
    if _manifest_cache is None:
        _manifest_cache = json.loads((BUILD / ".vite/manifest.json").read_text())
    return _manifest_cache


def _css_keys(theme: str) -> list[str]:
    """Manifest keys for the stylesheets to load for ``theme`` (empty = none)."""
    keys = [_BASE_CSS_KEY]
    if theme:
        keys.append(_THEME_CSS_KEY.format(theme=theme))
    return keys


def _shared_css_urls(vite_url: str | None, theme: str) -> list[str]:
    """URLs for the base and theme stylesheets.

    In dev the JS entries import these files, so Vite injects them; the
    backend does not link them, avoiding the HMR-wrapped module output.
    Themes added after the last frontend build are missing from the
    manifest — fall back to the base stylesheet rather than failing.
    """
    if vite_url:
        return []
    manifest = _manifest()
    return [f"/{manifest[key]['file']}" for key in _css_keys(theme) if key in manifest]


def _editor_css_url(vite_url: str | None, theme: str) -> str | None:
    """URL for the editor-specific stylesheet (Vue component styles).

    This is linked by the public-page edit pen so the editor styles are
    loaded before the editor JS dynamic-import resolves.
    """
    if vite_url:
        return None
    manifest = _manifest()
    entry = manifest["src/main.js"]
    shared_files = {manifest[key]["file"] for key in _css_keys(theme)}
    for css in entry.get("css", []):
        if css not in shared_files:
            return f"/{css}"
    return None


def _layout(
    urls: list[str],
    modules: list[str] = (),
    custom_css: str = "",
    theme: str = "",
) -> Template:
    """Page layout template with standard asset URLs and ES-module scripts.

    Stylesheets use ``blocking="render"`` so the browser waits for them before
    showing the page, avoiding a flash of unstyled content.

    The active theme is named in a meta tag so that in dev (where the
    backend links no stylesheets and Vite injects them from JS) the
    frontend entries know which theme CSS module to import.
    """
    doc = Document(E.Title, lang="en")
    if theme:
        doc.meta(name="pagerite:theme", content=theme)
    # Editor asset URLs for pagerite.js, which injects the 🖊️ edit pens
    # itself once it has validated the session (pages render identically
    # for everyone; editing is gated by the auth proxy in front of /_api).
    script, editor_css = _editor_assets(theme)
    doc.meta(name="pagerite:editor-src", content=script[-1])
    if editor_css:
        doc.meta(name="pagerite:editor-css", content=editor_css)
    for url in urls:
        doc.link(rel="stylesheet", href=url, blocking="render")
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


def _brand_link(brand: str) -> HTML:
    """Header brand link; omitted entirely when no brand is configured."""
    return HTML(str(E.a(brand, href="/", id="brand"))) if brand else HTML("")


def _title(slug: str, node: Node) -> str:
    """Menu label: the configured title, prettified slug, "Home" fallback."""
    return node.title or prettify(slug) or "Home"


def _nav_link(doc, menu: dict[str, Node], node: Node, path: str, current: str) -> None:
    """Render one <li> linking the node. Category labels (no content of
    their own) link straight to their first child page, so normal
    navigation bypasses the placeholder page at their own URL."""
    # A top-level item is current also when viewing any of its subpages.
    is_current = current == path or (path and current.startswith(f"{path}/"))
    href = f"/{path}"
    if node.content is None and (leaf := first_leaf(menu, path)) is not None:
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
    offer at least two published items. The front page, leaf pages and
    one-page sections get no aside element at all (rather than an empty
    or one-item box).
    """
    if not current:
        return HTML("")
    section = current.split("/", 1)[0]
    node = menu.get(section)
    if node is None:
        return HTML("")
    items = [(s, c) for s, c in sorted_nodes(node.children) if c.published]
    if len(items) < 2:
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
        if child.published and child.content is not None:
            return cpath
        if (leaf := _first_leaf(child, cpath)) is not None:
            return leaf
    return None


def banner_html(menu: dict[str, Node], path: str, theme: str = "") -> HTML:
    """Resolve the banner for a path: the nearest node on the ancestor
    chain (the node itself first), then the front page, then the theme
    artwork. The front page is a top-level *sibling* of the other
    main-level nodes, not their parent, so it never appears in the chain
    and is consulted explicitly, last. The snippet is raw trusted HTML,
    so a banner can be anything — an img, a styled div, canvas + script.

    With no user banner anywhere in the chain, the active theme's inline
    SVG artwork is inlined instead: as markup it can be recolored from the
    theme stylesheet (``var(--accent)`` etc.) and animated, and it is not
    rendered at all when the user supplies their own banner.
    """
    source = banner_source(menu, path)
    if source is not None:
        return HTML(resolve(menu, source)[-1].banner)
    return _theme_banner(theme)


_banner_cache: dict[str, HTML] = {}


def _theme_banner(theme: str) -> HTML:
    """The theme's inline banner SVG (empty for none/unknown themes)."""
    if not theme or "/" in theme:
        return HTML("")
    if theme not in _banner_cache:
        path = Path(__file__).parent / "themes" / theme / "banner.svg"
        _banner_cache[theme] = HTML(path.read_text()) if path.exists() else HTML("")
    return _banner_cache[theme]


def banner_source(menu: dict[str, Node], path: str) -> str | None:
    """Which node's banner applies at ``path``: the nearest ancestor with
    one set (the front page, a top-level sibling of the chain, last).
    None = the default artwork."""
    chain = resolve(menu, path) or []
    segs = path.split("/")
    for i in range(len(chain) - 1, -1, -1):
        if chain[i].banner:
            return "/".join(segs[: i + 1])
    front = menu.get("")
    if front and front.banner:
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
        doc.div(HTML(render(node.content or "", path)), class_="body")
    return HTML(str(doc))


def render_page(
    menu: dict[str, Node],
    path: str,
    brand: str = SITE_NAME,
    custom_css: str = "",
    theme: str = "",
) -> str:
    """Render a full HTML page for the slug path."""
    node = resolve(menu, path)[-1]
    title = _title(path.rpartition("/")[2], node)
    scripts, styles = _page_assets(theme)
    return str(
        _layout(styles, scripts, custom_css, theme)(
            Title=f"{title} – {brand}" if brand else title,
            Brand=_brand_link(brand),
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
    scripts, styles = _page_assets(theme)
    return str(
        _layout(styles, scripts, custom_css, theme)(
            Title=f"{title} – {brand}" if brand else title,
            Brand=_brand_link(brand),
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
) -> str:
    """Render a 404 page within the normal layout."""
    doc = E.article
    with doc:
        doc.h1("Not Found")
        doc.p(f"No page at /{path}.")
    scripts, styles = _page_assets(theme)
    return str(
        _layout(styles, scripts, custom_css, theme)(
            Title=f"Not Found – {brand}" if brand else "Not Found",
            Brand=_brand_link(brand),
            Nav=nav_html(menu, path),
            Sidebar=sidebar_html(menu, path),
            Banner=banner_html(menu, path, theme),
            Main=HTML(str(doc)),
        ),
    )


def _page_assets(theme: str) -> tuple[list[str], list[str]]:
    """Script and CSS URLs for public pages (pagerite entry).

    Dev mode loads the entry from the Vite dev server; production uses
    the Vite build manifest to resolve the hashed asset names.
    """
    vite_url = os.environ.get("PAGERITE_VITE_URL")
    if vite_url:
        return [f"{vite_url}/src/pagerite.js"], _shared_css_urls(vite_url, theme)
    key = f"page:{theme}"
    if key not in _asset_cache:
        manifest = _manifest()
        entry = manifest["src/pagerite.js"]
        _asset_cache[key] = (
            [f"/{entry['file']}"],
            _shared_css_urls(None, theme),
        )
    return _asset_cache[key]


def _editor_assets(theme: str) -> tuple[list[str], str | None]:
    """Script URL and editor-specific CSS URL for the public-page edit pen.

    The shared CSS is already linked on the page, so the pen only needs the
    editor-specific stylesheet.
    """
    vite_url = os.environ.get("PAGERITE_VITE_URL")
    if vite_url:
        return [f"{vite_url}/@vite/client", f"{vite_url}/src/main.js"], None
    key = f"editor:{theme}"
    if key not in _asset_cache:
        manifest = _manifest()
        entry = manifest["src/main.js"]
        _asset_cache[key] = [f"/{entry['file']}"], _editor_css_url(None, theme)
    return _asset_cache[key]
