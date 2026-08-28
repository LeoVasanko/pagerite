"""Kanta schema migrations, discovered by name (``migrate_vN``).

Each function receives the raw state dict (JSON-level: bytes are base64
strings) before it is decoded into ``Data`` structs, and runs exactly once
per database based on its recorded version.
"""

import base64


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
