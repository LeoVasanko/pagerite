"""Command-line entry point for running the backend server."""

import argparse
import gzip
import os
import sys
from datetime import date
from pathlib import Path

import httpx
from fastapi_vue import server

DEFAULT_PORT = 8100
DEVMODE = os.getenv("PAGERITE_DEV") == "1"

# Repository root (pagerite/__main__.py -> ..), where the MMDB lives.
_REPO_ROOT = Path(__file__).resolve().parent.parent

DBIP_URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"


def _download_dbip() -> None:
    """Download the latest dbip-city-lite MMDB if ours is missing or older."""
    today = date.today()
    months = [f"{today:%Y-%m}"]
    # The current month's file may not be published yet; fall back to last month.
    prev = (today.replace(day=1) - date.resolution).replace(day=1)
    months.append(f"{prev:%Y-%m}")

    existing = sorted(
        p.stem.removeprefix("dbip-city-lite-").removesuffix(".mmdb")
        for p in _REPO_ROOT.glob("dbip-city-lite-*.mmdb*")
    )
    if existing and existing[-1] >= months[0]:
        print(f"pagerite: DB-IP database is current ({existing[-1]}), skipping download")
        return

    for month in months:
        url = DBIP_URL.format(month=month)
        target = _REPO_ROOT / f"dbip-city-lite-{month}.mmdb.gz"
        tmp = target.with_suffix(".mmdb.gz.tmp")
        print(f"pagerite: downloading {url}")
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as e:
            print(f"pagerite: DB-IP download failed: {e}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            continue
        # Verify it is actually gzip data before installing it.
        try:
            with gzip.open(tmp, "rb") as f:
                f.read(1)
        except OSError:
            print(f"pagerite: DB-IP download for {month} was not valid gzip", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            continue
        os.replace(tmp, target)
        # Drop older databases so the app never picks up a stale one.
        for old in _REPO_ROOT.glob("dbip-city-lite-*.mmdb*"):
            if old.name != target.name:
                old.unlink()
        print(f"pagerite: DB-IP database updated to {target.name}")
        return
    print("pagerite: could not download a DB-IP database", file=sys.stderr)


def main() -> None:
    """Run the backend server with optional arguments."""
    parser = argparse.ArgumentParser(description="Run the pagerite server.")
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
    if args.dbip:
        _download_dbip()
    dev = {"reload": True, "reload_dirs": ["pagerite"]} if DEVMODE else {}
    server.run(
        "pagerite.app:app",
        listen=args.listen,
        default_port=DEFAULT_PORT,
        **dev,
    )


if __name__ == "__main__":
    main()
