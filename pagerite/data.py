"""Data model persisted in the kanta database.

The site structure is a tree of Nodes. Every node is a menu label with a
configurable title and slug (its key in the parent's ``children``); the
URL path is the chain of slugs from the top level. ``chunks`` is the
node's Markdown page as ordered content-hash keys into ``Data.chunks``
(docs/migrate.md), or None for a pure category label, whose URL renders
a placeholder page while nav links point at its first child.
"""

from datetime import UTC, datetime

import msgspec

from pagerite.chunks import join_chunks


class Patch(msgspec.Struct, omit_defaults=True):
    """One editing session's overrides on a translated view, applied
    independently per hunk (docs/localization.md)."""

    #: (search, replace) pairs on the served hybrid Markdown.
    hunks: list[tuple[str, str]] = []


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
    #: Ordered chunk hashes (9-byte keys into ``Data.chunks``); None =
    #: pure category label (its URL renders a placeholder page), a list
    #: (possibly empty) = a page.
    chunks: list[bytes] | None = None
    #: Primary language of the article (BCP-47 base tag). "" = inherit
    #: (nearest ancestor, front page last, site default "en" final).
    language: str = ""
    #: Chunk hashes the editor marked "do not translate" (always served
    #: from the original). Presence-keys, value always True.
    no_trans: dict[bytes, bool] = {}
    #: Languages this article is available in (besides its primary
    #: language). Presence-keys, value always True — the availability
    #: index for rendering and language selection; maintained by whoever
    #: writes translation data (docs/migrate.md).
    langs: dict[str, bool] = {}
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


class Data(msgspec.Struct):
    """Root object of the kanta database. Owned and edited in place by us."""

    #: Top-level menu items by slug; "" is the front page.
    menu: dict[str, Node] = {}
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
    theme: str = "corporate"
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
    #: API keys gating the translator service WebSocket (/_translate/{key};
    #: the external forward-auth does not cover that route): key -> display
    #: name. Keys are 12 lowercase alphanumeric characters; the first is
    #: generated at database bootstrap, more are managed in the editor
    #: shell's lang tab (via /_api/settings).
    translate_keys: dict[str, str] = {}
    #: Wanted target languages for the translator service (presence-keys,
    #: value always True). The dispatcher offers jobs only in the
    #: intersection of these and a connection's announced capabilities.
    #: Bootstrapped to es+zh; edited in the editor shell's localization
    #: tab (or via /_api/settings).
    translate_langs: dict[str, bool] = {}
    #: All original-language page text, content-addressed:
    #: chunk_key (9 bytes; base64 at the JSON level) -> Markdown chunk.
    #: Shared by every article.
    chunks: dict[bytes, str] = {}
    #: Machine translations: chunk hash -> lang -> translated Markdown
    #: (a nested dict rather than tuple keys, which msgspec's JSON
    #: serializer does not support). Also used for node titles (hash of
    #: the title text).
    trans: dict[bytes, dict[str, str]] = {}
    #: User override patches per article and language:
    #: f"{path}:{lang}" -> ordered patches (paths without leading slash).
    patches: dict[str, list[Patch]] = {}


def node_markdown(data: Data, node: Node) -> str | None:
    """The node's original Markdown assembled from the chunk store.

    None for category labels (chunks is None); an empty page gives "".
    Hashes missing from the store (shouldn't happen) are skipped.
    """
    if node.chunks is None:
        return None
    return join_chunks(
        [t for h in node.chunks if (t := data.chunks.get(h)) is not None]
    )


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
