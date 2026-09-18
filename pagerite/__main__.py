"""Command-line entry point for running the backend server."""

import argparse
import os
from pathlib import Path

import msgspec
from fastapi_vue import env, server

from pagerite.config import Config

DEFAULT_PORT = 8100
os.environ["FASTAPI_VUE"] = "PAGERITE"


def main() -> None:
    """Run the backend server with optional arguments."""
    parser = argparse.ArgumentParser(description="Run the pagerite server.")
    parser.add_argument(
        "hostname",
        nargs="?",
        default="localhost",
        help=(
            "Public hostname of the site; names the data directory "
            "<hostname>/{content.kantadb, analytics.json, files} under the "
            "cwd (default: localhost)."
        ),
    )
    parser.add_argument(
        "-l",
        "--listen",
        action="append",
        help=(f"Endpoint (default: localhost:{DEFAULT_PORT})."),
    )
    parser.add_argument(
        "--dbip",
        action="store_true",
        help="Download/update the DB-IP city lite database before starting.",
    )
    args = parser.parse_args()
    # Hand configuration to the app as JSON in PAGERITE_CONFIG; it must be
    # set before pagerite.app is imported, as state.py reads it at import
    # time (data directory, public origin).
    os.environ["PAGERITE_CONFIG"] = msgspec.json.encode(
        Config(hostname=args.hostname, dbip=args.dbip)
    ).decode()
    run_args: dict = {}
    if args.hostname != "localhost":
        # A public site sits behind TLS on its hostname; show that URL in the
        # startup box instead of the local listen address.
        run_args["startup_box"] = f"{{Name}} {{version}}\nhttps://{args.hostname}"
    server.run(
        "pagerite.app:app",
        listen=args.listen,
        default_port=DEFAULT_PORT,
        server_header=False,
        reload=Path(__file__).parent if env.dev else False,
        # Partial log config, merged over uvicorn's default by fastapi-vue:
        # root prints at WARNING in production / INFO in dev. Keep our own
        # loggers audible in production, and silence httpx's per-request INFO
        # (tracking._schedule_favicon_fetch logs its own one-line summary).
        log_config={
            "loggers": {
                "pagerite": {"level": "INFO"},
                "httpx": {"level": "WARNING"},
            }
        },
        **run_args,
    )


if __name__ == "__main__":
    main()
