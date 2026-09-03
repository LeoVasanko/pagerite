"""Kanta schema migrations, discovered by name (``migrate_vN``).

Each function receives the raw state dict (JSON-level: bytes are base64
strings, datetimes RFC 3339 strings, struct fields with default values
omitted) before it is decoded into ``Data`` structs, and runs exactly once
per database based on its recorded version.

All storage/schema upgrades live here — including on-disk file work, which
runs through files.py's file store (imported lazily: files.py owns the store
and state.py passes this module to Kanta; at migration time, during lifespan
``kanta.open()``, both modules are fully loaded).
"""

import base64
import re
from pathlib import Path

from pagerite.chunks import chunk_key, chunk_markdown
from pagerite.data import prettify


def _append_order(nodes: dict) -> float:
    """Raw-dict equivalent of data.append_order (order keys may be absent)."""
    return max((n.get("order", 0) for n in nodes.values()), default=0) + 1


def _ensure(menu: dict, path: str) -> dict:
    """Raw-dict equivalent of state._ensure: the node dict at ``path``,
    creating it and any missing ancestors (content-less category labels)
    appended at the end of their level."""
    nodes = menu
    node = None
    for seg in path.split("/"):
        node = nodes.get(seg)
        if node is None:
            node = {"title": prettify(seg), "order": _append_order(nodes)}
            nodes[seg] = node
        nodes = node.setdefault("children", {})
    return node


def migrate_v1(d: dict) -> None:
    """Move in-database file blobs to the on-disk content-addressed store,
    and rebuild the legacy flat page store (``pages``) as the menu tree."""
    files = d.pop("files", None)
    if files:
        from pagerite.files import file_store

        for name, body in files.items():
            if isinstance(body, str):  # JSON-level bytes are base64 strings
                body = base64.b64decode(body)
            file_store.put(name, body)
    pages = d.pop("pages", None)
    if not pages:
        return
    menu = d.setdefault("menu", {})
    for path, page in pages.items():
        node = _ensure(menu, path)
        node["title"] = page["title"]
        node["content"] = page["markdown"]
        for key in ("banner", "published", "order", "created", "modified"):
            if key in page:
                node[key] = page[key]


#: Extension-less file links: uploaded images are linked as /_f/<hash>
#: and the server negotiates avif/webp/jpg from the Accept header.
_DERIVATIVE_LINK = re.compile(r"(/_f/[0-9a-f]{12})\.(?:avif|webp)\b")


def _backfill_derivatives() -> None:
    """Create missing AVIF/WebP/JPEG derivatives for files stored before
    they were introduced (older uploads may have only the original plus
    AVIF, and SVGs no raster variants at all).  WebP/JPEG are re-encoded
    from an existing AVIF when available, everything else from the
    original (SVGs rasterized first)."""
    from pagerite.files import (
        IMAGE_MAXSIZE,
        IMAGE_WEBP_QUALITY,
        IMAGE_JPG_QUALITY,
        _avif_to_format,
        _svg_to_png,
        _to_avif,
        file_store,
    )

    try:
        paths = [f for f in file_store.path.iterdir() if f.is_file()]
    except FileNotFoundError:
        return
    groups: dict[str, list[Path]] = {}
    for p in paths:
        groups.setdefault(p.name.partition(".")[0], []).append(p)
    for digest, files in groups.items():
        names = {p.name for p in files}
        source = next(
            (p for p in files if ".orig." in p.name or p.suffix == ".svg"), None
        )
        if source is None:
            continue  # plain as-is file, no derivatives to make
        avif = file_store.get(f"{digest}.avif")
        if avif is None:
            ext = source.suffix
            body = source.read_bytes()
            if ext == ".svg":
                png = _svg_to_png(body, IMAGE_MAXSIZE)
                if png is None:
                    continue
                body, ext = png, ".png"
            converted = _to_avif(body, ext)
            if converted is None:
                continue
            file_store.put(f"{digest}.avif", converted)
            avif = file_store.get(f"{digest}.avif")
        for fmt, quality in (
            ("webp", IMAGE_WEBP_QUALITY),
            ("jpg", IMAGE_JPG_QUALITY),
        ):
            if f"{digest}.{fmt}" not in names:
                file_store.put(
                    f"{digest}.{fmt}", _avif_to_format(avif[0], f".{fmt}", quality)
                )


def migrate_v2(d: dict) -> None:
    """Extension-less image links: strip .avif/.webp extensions from /_f/
    links in page content and banners (the server now negotiates the format
    by Accept header), backfill missing AVIF/WebP/JPEG derivatives on disk,
    and drop the obsolete render-counter field ``version`` (invalidation is
    an in-memory concern now, not database state)."""

    def walk(nodes: dict) -> None:
        for node in nodes.values():
            for field in ("content", "banner"):
                if isinstance(node.get(field), str):
                    node[field] = _DERIVATIVE_LINK.sub(r"\1", node[field])
            walk(node.get("children") or {})

    walk(d.get("menu") or {})
    d.pop("version", None)
    _backfill_derivatives()


def migrate_v3(d: dict) -> None:
    """Content-addressed chunk storage (docs/migrate.md): split every
    node's string ``content`` into block chunks stored once per content
    hash in the new ``chunks`` store; the node keeps the ordered hash
    list as ``chunks`` (an absent content stays absent, i.e. None = a
    pure category label; "" chunks to an empty list = an empty page).

    Chunk keys are 9-byte blake3 digests; at this raw JSON level they are
    base64 strings (decoding into the structs restores ``bytes`` keys).
    ``trans``/``patches`` start empty; the translator job fills them and
    maintains the ``langs`` index as translations land. ``language``,
    ``no_trans`` and ``langs`` need nothing — struct defaults cover them.
    """
    store = d.setdefault("chunks", {})
    d.setdefault("trans", {})
    patches = d.setdefault("patches", {})

    def walk(nodes: dict) -> None:
        for node in nodes.values():
            content = node.pop("content", None)
            if isinstance(content, str):
                hashes = []
                for chunk in chunk_markdown(content):
                    key = base64.b64encode(chunk_key(chunk)).decode()
                    store.setdefault(key, chunk)
                    hashes.append(key)
                node["chunks"] = hashes
            walk(node.get("children") or {})

    walk(d.get("menu") or {})
    # Article paths never carry a leading slash in keys (docs/migrate.md).
    # The only path-keyed store starts empty here, so this is defensive
    # for databases that went through a downgrade/upgrade cycle.
    for key in [k for k in patches if k.startswith("/")]:
        patches[key.lstrip("/")] = patches.pop(key)
