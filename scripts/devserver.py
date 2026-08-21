#!/usr/bin/env -S uv run
"""Run Vite development server for Vue app and FastAPI backend with auto-reload."""

import argparse
import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path

# Import util.py from scripts/fastapi-vue (not a package, so we adjust sys.path)
sys.path.insert(0, str(Path(__file__).with_name("fastapi-vue")))
from devutil import (
    ProcessGroup,
    check_ports_free,
    logger,
    ready,
    setup_cli,
    setup_vite,
)

DEFAULT_VITE_PORT = 8200
DEFAULT_DEV_PORT = 8210
HEALTH = "/?from=devserver.py"


async def run_devserver(
    listen: str,
    backend: str,
    extra_args: list[str] | None = None,
) -> None:
    """Start Vite and FastAPI dev servers with hot reload."""
    reporoot = Path(__file__).parent.parent
    front = reporoot / "frontend"
    if not (front / "package.json").exists():
        logger.warning("Frontend source not found at %s", front)
        raise SystemExit(1)

    viteurl, npm_install, vite = setup_vite(listen, DEFAULT_VITE_PORT)
    backurl, pagerite = setup_cli("pagerite", backend, DEFAULT_DEV_PORT)

    # Tell the everyone by environment (vite proxy and backend devmode use these)
    os.environ["PAGERITE_VITE_URL"] = viteurl
    os.environ["PAGERITE_BACKEND_URL"] = backurl
    os.environ["PAGERITE_DEV"] = "1"

    async with ProcessGroup() as pg:
        npm_i = await pg.spawn(*npm_install, cwd=front)
        await check_ports_free(viteurl, backurl)
        await pg.spawn(*pagerite, *(extra_args or []))
        await pg.wait(npm_i, ready(backurl, path=HEALTH))
        await pg.spawn(*vite, cwd=front)


def main() -> None:
    """Parse CLI arguments and run the devserver."""
    parser = argparse.ArgumentParser(
        description="Run Vite and FastAPI development servers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG,
    )
    parser.add_argument(
        "-l",
        "--listen",
        metavar="addr",
        help=f"Vite (default: localhost:{DEFAULT_VITE_PORT})",
    )
    parser.add_argument(
        "--backend",
        metavar="addr",
        help=f"FastAPI (default: localhost:{DEFAULT_DEV_PORT})",
    )
    args, extra_args = parser.parse_known_args()
    with suppress(KeyboardInterrupt):
        asyncio.run(run_devserver(args.listen, args.backend, extra_args))


HELP_EPILOG = """
  Other options are forwarded to pagerite [args]

  JS_RUNTIME environment variable can be used to select the JS runtime:
  npm, deno, bun, or full path to the runtime executable (node maps to npm).
"""


if __name__ == "__main__":
    main()
