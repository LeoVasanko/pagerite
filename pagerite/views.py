"""HTML rendering: page layout template, navigation, content pages.

All pages share one static layout, defined once as an html5tagger Template
with placeholders (capitalized attributes) filled per request. The dynamic
regions carry stable ids (#nav, #main) so that the fetch-navigation script
can swap them without reloading the page chrome.

Navigation walks the Node tree directly (see data.py): nav_html lists the
top level — the front page (slug "") is an ordinary top-level item, not
the parent of the others — and sidebar_html the children of the current
top-level section. Nodes without content are category labels; nav links
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


def _layout(urls: list[str], modules: list[str] = ()) -> Template:
    """Page layout template with standard asset URLs and ES-module scripts."""
    doc = Document(E.Title, lang="en", _urls=urls)
    for src in modules:
        doc.script(src=src, type="module", defer=True)
    return Template(
        doc
        .header(
            E.div(E.Banner, id="page-banner"),
            E.BannerEdit,
            E.Brand,
            E.nav(E.Nav, id="nav"),
            id="banner",
        )
        .div(
            E.aside(E.Sidebar, id="sidebar"),
            E.main(E.Main, id="main"),
            id="content",
        )
        .footer(None),  # kept empty for now; zero-height (see style.css)
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
    """Render the contents of the #sidebar element for the current path.

    Lists the direct children of the current main level section; empty when
    the path is not inside a section or the section has no children.
    """
    if not current:
        return HTML("")
    section = current.split("/", 1)[0]
    node = menu.get(section)
    if node is None:
        return HTML("")
    nav = E.ul
    with nav:
        for slug, child in sorted_nodes(node.children):
            if child.published:
                _nav_link(nav, menu, child, f"{section}/{slug}", current)
    return HTML(str(nav))


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


def banner_html(menu: dict[str, Node], path: str) -> HTML:
    """Resolve the banner for a path: the nearest node on the ancestor
    chain (the node itself first), then the front page, then the default
    CSS artwork. The front page is a top-level *sibling* of the other
    main-level nodes, not their parent, so it never appears in the chain
    and is consulted explicitly, last. The snippet is raw trusted HTML,
    so a banner can be anything — an img, a styled div, canvas + script.
    """
    source = banner_source(menu, path)
    if source is None:
        return HTML("")
    return HTML(resolve(menu, source)[-1].banner)


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


def _edit_attrs(path: str, mode: str = "page") -> dict:
    """Attributes for a 🖊️ edit button.

    pagerite.js wires these buttons to dynamic-import the editor app
    (data-editor-src, plus any extra styles it needs) and open the docked
    editor without leaving the page. mode="page" edits content; mode="site"
    (the pen on the banner) edits the banner and site structure. They are
    buttons, not links: editing is an action, not a navigation.
    """
    scripts, styles = _editor_assets()
    return {
        "type": "button",
        "class": "edit-link" if mode == "page" else "edit-link banner-edit-link",
        "title": "edit",
        "data-editor-src": scripts[-1],
        "data-editor-css": ",".join(styles),
        "data-editor-mode": mode,
    }


def page_content(menu: dict[str, Node], path: str) -> HTML:
    """Render the contents of the #main element for a page."""
    node = resolve(menu, path)[-1]
    doc = E.article
    with doc:
        # An h1 in the markdown owns the article heading; the title is
        # only rendered as h1 when the markdown has none of its own.
        if not has_h1(node.content or ""):
            doc.h1(node.title)
        # All users are trusted authors for now, so the edit button is public.
        doc.button("🖊️", **_edit_attrs(path))
        doc.div(HTML(render(node.content or "", path)), class_="body")
    return HTML(str(doc))


def render_page(menu: dict[str, Node], path: str, brand: str = SITE_NAME) -> str:
    """Render a full HTML page for the slug path."""
    node = resolve(menu, path)[-1]
    title = _title(path.rpartition("/")[2], node)
    scripts, styles = _page_assets()
    return str(
        _layout(styles, scripts)(
            Title=f"{title} – {brand}" if brand else title,
            Brand=_brand_link(brand),
            Nav=nav_html(menu, path),
            Sidebar=sidebar_html(menu, path),
            Banner=banner_html(menu, path),
            BannerEdit=HTML(str(E.button("🖊️", **_edit_attrs(path, "site")))),
            Main=page_content(menu, path),
        ),
    )


def render_category(menu: dict[str, Node], path: str, brand: str = SITE_NAME) -> str:
    """Render the placeholder for a content-less category label (404).

    The node exists in the tree but has no page of its own. Nav links
    point straight at its first child, so this is mainly seen in the site
    editor, where the pen creates the landing page.
    """
    node = resolve(menu, path)[-1]
    title = _title(path.rpartition("/")[2], node)
    doc = E.article
    with doc:
        doc.h1(title)
        # Editing works here too: the pen creates this category's page.
        doc.button("🖊️", **_edit_attrs(path))
        doc.p(
            "Pages in this section are listed in the menu on the left."
        )
    scripts, styles = _page_assets()
    return str(
        _layout(styles, scripts)(
            Title=f"{title} – {brand}" if brand else title,
            Brand=_brand_link(brand),
            Nav=nav_html(menu, path),
            Sidebar=sidebar_html(menu, path),
            Banner=banner_html(menu, path),
            BannerEdit=HTML(str(E.button("🖊️", **_edit_attrs(path, "site")))),
            Main=HTML(str(doc)),
        ),
    )


def render_not_found(menu: dict[str, Node], path: str, brand: str = SITE_NAME) -> str:
    """Render a 404 page within the normal layout."""
    doc = E.article
    with doc:
        doc.h1("Not Found")
        # Editing works here too: this is how brand new pages get created.
        doc.button("🖊️", **_edit_attrs(path))
        doc.p(f"No page at /{path}.")
    scripts, styles = _page_assets()
    return str(
        _layout(styles, scripts)(
            Title=f"Not Found – {brand}" if brand else "Not Found",
            Brand=_brand_link(brand),
            Nav=nav_html(menu, path),
            Sidebar=sidebar_html(menu, path),
            Banner=banner_html(menu, path),
            BannerEdit=HTML(str(E.button("🖊️", **_edit_attrs(path, "site")))),
            Main=HTML(str(doc)),
        ),
    )


def _page_assets() -> tuple[list[str], list[str]]:
    """Script and CSS URLs for public pages (pagerite entry).

    Dev mode loads the entry from the Vite dev server; production uses
    the Vite build manifest to resolve the hashed asset names.
    """
    if vite_url := os.environ.get("PAGERITE_VITE_URL"):
        return (
            [f"{vite_url}/src/pagerite.js"],
            [],  # Vite injects the imported CSS in dev
        )
    manifest = json.loads((BUILD / ".vite/manifest.json").read_text())
    entry = manifest["src/pagerite.js"]
    # Manifest paths already carry the _/assets/ prefix (assetsDir).
    styles = [f"/{css}" for css in entry.get("css", [])]
    return [f"/{entry['file']}"], styles


def _editor_assets() -> tuple[list[str], list[str]]:
    """Script and CSS URLs for the admin editor (main entry).

    Dev mode loads the modules from the Vite dev server; production uses
    the Vite build manifest to resolve the hashed asset names.
    """
    if vite_url := os.environ.get("PAGERITE_VITE_URL"):
        return (
            [f"{vite_url}/@vite/client", f"{vite_url}/src/main.js"],
            [],  # Vite injects the imported CSS in dev
        )
    manifest = json.loads((BUILD / ".vite/manifest.json").read_text())
    entry = manifest["src/main.js"]
    styles = [f"/{css}" for css in entry.get("css", [])]
    return [f"/{entry['file']}"], styles


def render_editor() -> str:
    """Render the admin editor shell: a mount point for the Vue app."""
    scripts, styles = _editor_assets()
    doc = Document(f"Admin – {SITE_NAME}", lang="en", _urls=styles)
    for src in scripts:
        doc.script(src=src, type="module", defer=True)
    doc.div(None, id="app")
    return str(doc)
