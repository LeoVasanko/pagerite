# auto-upgrade@fastapi-vue-setup - remove this if you modify this file
"""Command-line entry point for running the backend server."""

import argparse
import os

from fastapi_vue import server

DEFAULT_PORT = 3100
DEVMODE = os.getenv("PAGERITE_DEV") == "1"


def main() -> None:
    """Run the backend server with optional arguments."""
    parser = argparse.ArgumentParser(description="Run the pagerite server.")
    parser.add_argument(
        "-l",
        "--listen",
        action="append",
        help=(f"Endpoint (default: localhost:{DEFAULT_PORT})."),
    )
    args = parser.parse_args()
    dev = {"reload": True, "reload_dirs": ["pagerite"]} if DEVMODE else {}
    server.run(
        "pagerite.app:app",
        listen=args.listen,
        default_port=DEFAULT_PORT,
        **dev,
    )


if __name__ == "__main__":
    main()
