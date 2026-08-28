"""Data model persisted in the kanta database.

The site structure is a tree of Nodes. Every node is a menu label with a
configurable title and slug (its key in the parent's ``children``); the
URL path is the chain of slugs from the top level. ``content`` is the
node's Markdown page, or None for a pure category label, whose URL renders
a placeholder page while nav links point at its first child.
"""

from datetime import UTC, datetime

import msgspec


class Node(msgspec.Struct, omit_defaults=True):
    """One item of the site hierarchy.

    Siblings are ordered by the fractional ``order`` key (never list
    positions): a moved item takes a fresh key relative to its new
    siblings, all other items keep theirs.

    The front page is whichever top-level node has slug "" (URL "/") — an
    item parallel to the other main-level pages, not their parent, so it
    cannot have children. Renaming it away leaves no front page ("/"
    redirects to the first nav item); any childless top-level node can
    take the empty slug.
    """

    title: str = ""
    order: float = 0
    #: Markdown source of the node's page; None = pure category label
    #: (its URL renders a placeholder page).
    content: str | None = None
    #: Raw HTML for the header banner (img, styled div, canvas+script...),
    #: rendered after the banner design's artwork so author code always
    #: wins over the design's own styles.
    #: Empty inherits the nearest ancestor's banner, front page last.
    banner: str = ""
    #: Banner design: a theme folder name (its banner.css/banner.svg),
    #: "" = explicitly no design, None = inherit (nearest ancestor, front
    #: page last, then the active theme's own design).
    banner_design: str | None = None
    published: bool = True
    children: dict[str, "Node"] = {}
    created: datetime = msgspec.field(
        default_factory=lambda: datetime.now(UTC),
    )
    modified: datetime = msgspec.field(
        default_factory=lambda: datetime.now(UTC),
    )


class Page(msgspec.Struct, omit_defaults=True):
    """Legacy flat page record, from before the tree model.

    Kept only so old databases still decode; app.py migrates any entries
    into ``Data.menu`` on startup and clears this.
    """

    title: str
    markdown: str
    published: bool = True
    order: float = 0
    banner: str = ""
    created: datetime = msgspec.field(
        default_factory=lambda: datetime.now(UTC),
    )
    modified: datetime = msgspec.field(
        default_factory=lambda: datetime.now(UTC),
    )


class Data(msgspec.Struct):
    """Root object of the kanta database. Owned and edited in place by us."""

    #: Top-level menu items by slug; "" is the front page.
    menu: dict[str, Node] = {}
    #: Bumped on every structure/content write, so page ETags (which embed
    #: it) invalidate cached copies when navigation-affecting changes happen.
    version: int = 0
    #: Site name shown in the header and <title> suffix; editable in the
    #: site editor. Empty = no brand link in the header, no title suffix.
    brand: str = "Pagerite"
    #: Raw trusted HTML replacing the brand link entirely (a logo image,
    #: styled markup, canvas+script...), site-wide — not per-page
    #: overridable like banners. Rendered in the header on top of the
    #: banner artwork, next to the nav. Empty = the plain brand link.
    brand_html: str = ""
    #: Active theme name (empty = none/base only). Themes live in
    #: pagerite/themes/{theme}/ (theme.css and/or banner.css/banner.svg/
    #: banner.html), served by the backend from disk.
    theme: str = "purple"
    #: Page transition design name (cube, crossfade, ...). Designs live in
    #: pagerite/themes/{name}/transition.css and are injected as
    #: #pagerite-transition on every page.
    transition: str = "cube"
    #: Raw site-wide custom CSS, injected inline in every page <head>.
    #: Trusted author content; not sanitized.
    custom_css: str = ""
    #: Favicon: content-addressed file name (served at "/_f/{name}"),
    #: linked as <link rel="icon"> on every page. Empty = the build's
    #: /favicon.ico.
    favicon: str = ""
    #: Legacy flat page store (pre-tree databases); migrated into `menu`
    #: on startup, then cleared. Never written otherwise.
    pages: dict[str, Page] = {}


def prettify(slug: str) -> str:
    """Human-readable default title for a slug segment."""
    return slug.replace("-", " ").replace("_", " ").title()


def resolve(menu: dict[str, Node], path: str) -> list[Node] | None:
    """Chain of nodes from the top level down to ``path`` ("" = front page).

    chain[0] is a top-level node, chain[-1] the node itself — the chain is
    useful for banner inheritance. None when any segment is missing.
    """
    chain = []
    nodes = menu
    for seg in path.split("/"):
        node = nodes.get(seg)
        if node is None:
            return None
        chain.append(node)
        nodes = node.children
    return chain


def find_slot(menu: dict[str, Node], path: str) -> tuple[dict[str, Node], str] | None:
    """The (children dict, slug) slot holding the node at ``path``.

    The returned dict is live: deleting or inserting the slug moves the
    node (its whole subtree travels with it). None when the parent chain
    does not resolve.
    """
    segs = path.split("/")
    nodes = menu
    for seg in segs[:-1]:
        node = nodes.get(seg)
        if node is None:
            return None
        nodes = node.children
    return nodes, segs[-1]


def sorted_nodes(nodes: dict[str, Node]) -> list[tuple[str, Node]]:
    """(slug, node) pairs in menu order: fractional order key, then title."""
    return sorted(nodes.items(), key=lambda kv: (kv[1].order, kv[1].title.lower()))


def append_order(nodes: dict[str, Node]) -> float:
    """Order value appending an item at the end of a sibling level."""
    return max((n.order for n in nodes.values()), default=0) + 1
