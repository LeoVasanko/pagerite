"""Kanta schema migrations, discovered by name (``migrate_vN``).

Each function receives the raw state dict (JSON-level: bytes are base64
strings) before it is decoded into ``Data`` structs, and runs exactly once
per database based on its recorded version.
"""

import base64
import re

#: Extension-less file links: uploaded images are now linked as /_f/<hash>
#: and the server negotiates avif/webp from the Accept header.
_DERIVATIVE_LINK = re.compile(r"(/_f/[0-9a-f]{12})\.(?:avif|webp)\b")


def _rewrite_links(text: str | None) -> str | None:
    return None if text is None else _DERIVATIVE_LINK.sub(r"\1", text)


def migrate_v1(d: dict) -> None:
    """Move in-database file blobs to the on-disk content-addressed store."""
    files = d.pop("files", None)
    if not files:
        return
    # Deferred import: app.py owns the file store and passes this module to
    # Kanta; at migration time (lifespan open) the module is fully loaded.
    from pagerite.app import file_store

    for name, body in files.items():
        if isinstance(body, str):  # JSON-level bytes are base64 strings
            body = base64.b64decode(body)
        file_store.put(name, body)


def _rewrite_links(text: str) -> str:
    return _DERIVATIVE_LINK.sub(r"\1", text)


def migrate_v2(d: dict) -> None:
    """Strip .avif/.webp extensions from /_f/ links in page content and
    banners (extension-less URLs negotiate the format by Accept header)."""

    def walk(nodes: dict) -> None:
        for node in nodes.values():
            for field in ("content", "banner"):
                if isinstance(node.get(field), str):
                    node[field] = _rewrite_links(node[field])
            walk(node.get("children") or {})

    walk(d.get("menu") or {})
