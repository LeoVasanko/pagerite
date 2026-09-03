"""CLI → app configuration, passed as JSON in the ``PAGERITE_CONFIG`` env var.

Kept dependency-free (msgspec only) so ``__main__`` can build and serialize
the config before any app module is imported, and the app side parses the
same struct back.  Import-time safe: nothing here reads the environment
until ``load()`` is called.
"""

import os

import msgspec


class Config(msgspec.Struct):
    """Configuration passed from the CLI entry point to the app."""

    #: Public hostname of the site; names the per-site data directory
    #: ``<hostname>/{content.kantadb, analytics.json, files}`` under the cwd.
    hostname: str = "localhost"
    #: Download/update the DB-IP city lite database at startup (--dbip).
    dbip: bool = False


def load() -> Config:
    """Parse ``PAGERITE_CONFIG``, or the defaults when unset."""
    if raw := os.getenv("PAGERITE_CONFIG"):
        return msgspec.json.decode(raw.encode(), type=Config)
    return Config()
