"""Command-line entry point for running the backend server."""

import argparse
import os
from pathlib import Path

from fastapi_vue import server
from fastapi_vue.hostutil import parse_endpoints

DEFAULT_PORT = 8100
DEVMODE = os.getenv("PAGERITE_DEV") == "1"


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
    # Export the hostname before pagerite.app is imported: it derives the
    # data directory and public origin from it at import time.
    os.environ["PAGERITE_HOSTNAME"] = args.hostname
    # And the listen port: the app prints the translator WS URL at startup,
    # which for localhost includes the actual port.
    for endpoint in parse_endpoints(args.listen, DEFAULT_PORT):
        if "port" in endpoint:
            os.environ["PAGERITE_PORT"] = str(endpoint["port"])
            break
    # --dbip: the app lifespan downloads/updates the DB-IP database, where
    # logging is already set up.
    if args.dbip:
        os.environ["PAGERITE_DBIP"] = "1"
    server.run(
        "pagerite.app:app",
        listen=args.listen,
        default_port=DEFAULT_PORT,
        server_header=False,
        reload=Path(__file__).parent if DEVMODE else False,
    )


if __name__ == "__main__":
    main()
