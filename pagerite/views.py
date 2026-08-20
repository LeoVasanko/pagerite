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
from html import unescape
import json
import os
import re

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
    stylesheets: list[str] = (),
    custom_css: str = "",
    theme: str = "",
    banner_design: str = "",
    favicon: str = "",
    social: dict[str, str] | None = None,
    extra_meta: dict[str, str] | None = None,
) -> Template:
    """Page layout template with standard asset URLs and ES-module scripts.

    Stylesheets use ``blocking="render"`` so the browser waits for them before
    showing the page, avoiding a flash of unstyled content. Order matters and
    is fixed: base (Vite build, absent in dev where Vite injects it from JS),
    theme and banner design (backend-served from pagerite/themes/), entry-
    specific stylesheets (e.g. overlayscrollbars.css), then the user's custom
    CSS last so it always wins.

    In dev, pagerite.js re-appends the backend-rendered theme/design links
    (and the custom CSS) after the Vite-injected base styles, keeping this
    order intact.

    ``social`` maps meta keys to contents: ``og:*``/``article:*`` go out as
    property attributes, everything else (description, twitter:*) as name.

    ``extra_meta`` is emitted as plain ``<meta name="..." content="...">``
    tags after the editor meta tags; used for page-specific import hints.
    """
    doc = Document(E.Title, lang="en")
    # Responsive layout (see the 48rem breakpoint in pagerite.css) needs
    # the real device width, not the default 980px layout viewport.
    doc.meta(name="viewport", content="width=device-width, initial-scale=1")
    for key, value in (social or {}).items():
        if value:
            if key.startswith(("og:", "article:")):
                doc.meta(property=key, content=value)
            elif key == "canonical":
                doc.link(rel="canonical", href=value)
            else:
                doc.meta(name=key, content=value)
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
    for key, value in (extra_meta or {}).items():
        doc.meta(name=key, content=value)
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
    for url in stylesheets:
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


def _nav_link(
    doc, menu: dict[str, Node], node: Node, path: str, current: str,
    ancestors_current: bool = True,
) -> None:
    """Render one <li> linking the node. Category labels (no content of
    their own — None, or empty markdown as left by the site editor's
    page creation) link straight to their first child page, so normal
    navigation bypasses the placeholder/empty page at their own URL."""
    # The navbar highlights a top-level item also when viewing any of its
    # subpages; the sidebar highlights only the actually viewed page.
    is_current = current == path or (
        ancestors_current and path and current.startswith(f"{path}/")
    )
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

    The sidebar is the current main level section's sub-navigation: the
    section's direct children as the top list level, with each item's own
    published children nested under it (third level and deeper), so it
    exists only when there is something to navigate: the section must
    offer at least two published items, or exactly one while viewing
    anything else than that only page — the section index, a 404, a
    grandchild (otherwise those pages offer no way to reach the child) —
    and viewing that only page itself still shows the sidebar when the
    page has published children of its own to reach.
    The front page, leaf pages, the sole childless page of a one-page
    section and childless sections get no aside element at all (rather
    than an empty or useless one-item box).
    """
    if not current:
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
            _sidebar_item(nav, menu, child, f"{section}/{slug}", current)
    return HTML(str(E.aside(nav, id="sidebar")))


def _sidebar_item(doc, menu: dict[str, Node], node: Node, path: str, current: str) -> None:
    """One sidebar <li>: the node link, with its published children as a
    nested list (third level and deeper, recursively)."""
    _nav_link(doc, menu, node, path, current, ancestors_current=False)
    sub = [(s, c) for s, c in sorted_nodes(node.children) if c.published]
    if sub:
        # doc.li.a(...) above left the <li> open for nesting.
        with doc.ul:
            for slug, child in sub:
                _sidebar_item(doc, menu, child, f"{path}/{slug}", current)


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
        if not child.published:
            continue
        cpath = f"{path}/{slug}" if path else slug
        if child.content:
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


def theme_banner_design(theme: str) -> str:
    """The theme's own banner design (a theme folder doubles as a banner
    design when it ships banner.css, banner.svg or banner.html), "" if it
    has none."""
    if _valid_name(theme) and any(
        (THEMES / theme / f).exists()
        for f in ("banner.css", "banner.svg", "banner.html")
    ):
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


_FIRST_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TAG = re.compile(r"<[^>]+>")
_IMG_TAG = re.compile(r"<img\b[^>]*>")
_VIDEO_TAG = re.compile(r"<video\b[^>]*>")
_ATTR_SRC = re.compile(r'src="([^"]+)"')
_ATTR_CLASS = re.compile(r'class="([^"]*)"')


def _share_media(html: str, base_url: str) -> tuple[str, str]:
    """(image, video) share URLs from the rendered article.

    Image preference: an image with class "hero" (author override, may
    appear anywhere in the article), then the first raster image (SVGs
    rasterize poorly or not at all on many social scrapers), then the
    first SVG. Video: the first <video> — og:video is in the OGP spec and
    honored mainly by Facebook; X/Twitter ignores it. Absolute URLs are
    built from the request base, scrapers cannot use relative ones.
    """
    if not base_url:
        return "", ""

    def absolute(src: str) -> str:
        src = unescape(src)
        return src if src.startswith(("http://", "https://")) else f"{base_url}{src}"

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
    image = hero or raster or svg
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
    """
    url = f"{base_url}/{path}" if base_url else ""
    m = _FIRST_P.search(html)
    text = unescape(_TAG.sub("", m.group(1) if m else ""))
    text = " ".join(text.split())
    if len(text) > 200:
        text = text[:200].rsplit(" ", 1)[0] + "…"
    image, video = _share_media(html, base_url)
    return {
        "description": text,
        "canonical": url,
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
    }


def render_page(
    menu: dict[str, Node],
    path: str,
    brand: str = SITE_NAME,
    custom_css: str = "",
    theme: str = "",
    favicon: str = "",
    brand_html: str = "",
    base_url: str = "",
) -> str:
    """Render a full HTML page for the slug path."""
    node = resolve(menu, path)[-1]
    title = _title(path.rpartition("/")[2], node)
    main = page_content(menu, path)
    social = _social_meta(node, path, title, str(main), brand, base_url)
    return str(
        _layout(
            *_page_assets(), custom_css, theme, banner_design(menu, path, theme),
            favicon, social,
        )(
            Title=f"{title} – {brand}" if brand else title,
            Brand=_brand_link(brand, brand_html),
            Nav=nav_html(menu, path),
            Sidebar=sidebar_html(menu, path),
            Banner=banner_html(menu, path, theme),
            Main=main,
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
        _layout(*_page_assets(), custom_css, theme, banner_design(menu, path, theme), favicon)(
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
        _layout(*_page_assets(), custom_css, theme, banner_design(menu, path, theme), favicon)(
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
) -> str:
    """Render the analytics viewer as a normal page at /_a."""
    page_scripts, page_stylesheets = _page_assets()
    analytics_scripts, analytics_stylesheets = _analytics_assets()
    scripts = page_scripts + analytics_scripts
    stylesheets = page_stylesheets + analytics_stylesheets
    doc = E.article
    with doc:
        doc.div(id="analytics-app")
    return str(
        _layout(
            scripts,
            stylesheets,
            custom_css,
            theme,
            banner_design(menu, "_a", theme),
            favicon,
            extra_meta={"pagerite:analytics-src": analytics_scripts[0]},
        )(
            Title=f"Analytics – {brand}" if brand else "Analytics",
            Brand=_brand_link(brand, brand_html),
            Nav=nav_html(menu, "_a"),
            Sidebar=sidebar_html(menu, "_a"),
            Banner=banner_html(menu, "_a", theme),
            Main=HTML(str(doc)),
        ),
    )
