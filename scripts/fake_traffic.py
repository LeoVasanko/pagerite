#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "httpx>=0.28.1",
#     "playwright>=1.45.0",
# ]
# ///
"""Generate fake browser visits and crawler hits for a Pagerite site.

The script drives a real Chromium browser with Playwright, clicking visible
internal links so the site's own analytics JavaScript records normal visits
(POST /_a).  Browser sessions and crawler GETs send a small rotating pool of
real public IPs in X-Forwarded-For, so the backend can reverse-DNS and GeoIP
them instead of seeing every hit as 127.0.0.1.

Sessions start with a Poisson inter-arrival delay (``--arrival-rate``) to
spread traffic out a little, while still keeping the overall run fast.

Run against a local dev server, e.g.:

    uv run scripts/fake_traffic.py http://localhost:3200 -b 8 -c 20

Repeat whenever you want more traffic; each run appends new events to the
site's analytics file.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("fake-traffic")


@dataclass(frozen=True)
class BrowserProfile:
    name: str
    user_agent: str
    accept_language: str
    viewport: tuple[int, int]


@dataclass(frozen=True)
class CrawlerProfile:
    name: str
    user_agent: str


BROWSER_PROFILES: list[BrowserProfile] = [
    BrowserProfile(
        "chrome-desktop",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "en-US,en;q=0.9",
        (1366, 768),
    ),
    BrowserProfile(
        "safari-desktop",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "en-GB,en;q=0.9",
        (1440, 900),
    ),
    BrowserProfile(
        "firefox-desktop",
        "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
        "en-CA,en;q=0.8,fr;q=0.5",
        (1920, 1080),
    ),
    BrowserProfile(
        "chrome-mobile",
        "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
        "es-ES,es;q=0.9",
        (390, 844),
    ),
]

CRAWLER_PROFILES: list[CrawlerProfile] = [
    CrawlerProfile(
        "googlebot",
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; +http://www.google.com/bot.html) Chrome/128.0.0.0 Safari/537.36",
    ),
    CrawlerProfile(
        "bingbot",
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm) Chrome/128.0.0.0 Safari/537.36",
    ),
    CrawlerProfile(
        "duckduckbot", "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)"
    ),
    CrawlerProfile("curl", "curl/8.5.0"),
]

# Small pool of real public resolver IPs. They have real reverse-DNS and GeoIP
# entries, and cycling through a handful avoids hammering DNS during traffic
# generation.
SOURCE_IPS: list[str] = [
    "8.8.8.8",
    "1.1.1.1",
    "9.9.9.9",
    "208.67.222.222",
    "185.228.168.9",
    "94.140.14.14",
]


def _sleep(base: float, jitter: float) -> None:
    time.sleep(max(0.0, base + random.uniform(-jitter, jitter)))


def _source_ip(index: int) -> str:
    """Pick one of the small pool of real public IPs."""
    return SOURCE_IPS[index % len(SOURCE_IPS)]


def _poisson_wait(rate: float) -> float:
    """Return an exponential inter-arrival time for the given Poisson rate."""
    if rate <= 0:
        return 0.0
    return random.expovariate(rate)


def _collect_links(page: Any) -> list[dict[str, Any]]:
    """Return internal links from the current page, excluding the current page."""
    return page.evaluate(
        """() => {
            const loc = new URL(location.href);
            return Array.from(document.querySelectorAll('a[href]'))
                .filter(a => {
                    try {
                        const u = new URL(a.href);
                        return u.origin === loc.origin
                            && !u.pathname.startsWith('/_')
                            && !u.pathname.startsWith('/auth')
                            && u.pathname !== '/favicon.ico'
                            && u.pathname !== loc.pathname;
                    } catch { return false; }
                })
                .map(a => {
                    const rect = a.getBoundingClientRect();
                    return {
                        href: a.href,
                        text: (a.innerText || a.title || '').trim().slice(0, 60),
                        visible: !!(rect.width && rect.height && rect.top < window.innerHeight && rect.bottom > 0),
                    };
                });
        }"""
    )


def _click_link(page: Any, link: dict[str, Any], timeout: float = 10.0) -> bool:
    """Click an internal link and wait for the client-side URL to change."""
    start_url = page.url
    try:
        # Prefer Playwright's native click; fall back to a JS click if the
        # locator cannot be resolved or times out.
        try:
            page.locator(f"a[href='{link['href']}']").first.click(timeout=2000)
        except Exception:  # noqa: BLE001
            clicked = page.evaluate(
                """(href) => {
                    const a = Array.from(document.querySelectorAll('a[href]'))
                        .find(el => el.href === href);
                    if (a) { a.click(); return true; }
                    return false;
                }""",
                link["href"],
            )
            if not clicked:
                return False
        # Wait for the client-side navigation to update the URL.
        deadline = time.time() + timeout
        while time.time() < deadline:
            if page.url != start_url:
                return True
            page.wait_for_timeout(100)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug("click failed on %s: %s", link.get("href"), exc)
        return False


def _run_browser_session(
    base: str,
    paths: Sequence[str],
    profile: BrowserProfile,
    session_index: int,
    max_clicks: int,
    stay: tuple[float, float],
    headless: bool,
    fake_ip: str,
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    trail: list[str] = []
    start_time = datetime.now(UTC)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent=profile.user_agent,
                viewport={"width": profile.viewport[0], "height": profile.viewport[1]},
                extra_http_headers={
                    "X-Forwarded-For": fake_ip,
                    "Accept-Language": profile.accept_language,
                },
            )
            page = context.new_page()
            entry = random.choice(paths) if paths else "/"
            page.goto(urljoin(base, entry), wait_until="networkidle")
            trail.append(page.url)

            for _ in range(max_clicks):
                _sleep(random.uniform(*stay) / 2, 0.3)
                links = _collect_links(page)
                visible = [item for item in links if item.get("visible")]
                if not visible:
                    visible = links
                if not visible:
                    break
                link = random.choice(visible)
                ok = _click_link(page, link)
                if not ok:
                    # Retry once with any link (sometimes visible calc misses nav).
                    alt = random.choice(links) if links else None
                    if alt and alt is not link:
                        ok = _click_link(page, alt)
                    if not ok:
                        break
                page.wait_for_load_state("networkidle")
                trail.append(page.url)
                _sleep(random.uniform(*stay), 0.5)

            browser.close()

        return {
            "profile": profile.name,
            "entry": entry,
            "ip": fake_ip,
            "pages": len(trail),
            "trail": [urlparse(u).path or "/" for u in trail],
            "duration": (datetime.now(UTC) - start_time).total_seconds(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("browser session failed: %s", exc)
        return {"profile": profile.name, "error": str(exc), "trail": trail}


def _run_crawler_hit(
    base: str,
    paths: Sequence[str],
    profile: CrawlerProfile,
    profile_index: int,
    session_index: int,
) -> dict[str, Any]:
    path = random.choice(paths) if paths else "/"
    url = urljoin(base, path)
    fake_ip = _source_ip(session_index)
    headers = {
        "User-Agent": profile.user_agent,
        "X-Forwarded-For": fake_ip,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            r = client.get(url, headers=headers)
        return {
            "profile": profile.name,
            "path": path,
            "status": r.status_code,
            "ip": fake_ip,
        }
    except Exception as exc:  # noqa: BLE001
        return {"profile": profile.name, "path": path, "error": str(exc)}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate fake traffic for a Pagerite site.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("url", help="Base URL of the Pagerite site")
    parser.add_argument(
        "-b",
        "--browsers",
        type=int,
        default=5,
        help="Number of simulated browser sessions",
    )
    parser.add_argument(
        "-c", "--crawlers", type=int, default=10, help="Number of crawler HTTP GETs"
    )
    parser.add_argument(
        "--max-clicks",
        type=int,
        default=6,
        help="Max internal link clicks per browser session",
    )
    parser.add_argument(
        "--stay",
        type=float,
        nargs=2,
        default=[2.0, 6.0],
        metavar=("MIN", "MAX"),
        help="Seconds to stay on a page before clicking again",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run browsers headlessly",
    )
    parser.add_argument(
        "--arrival-rate",
        type=float,
        default=1.0,
        help="Average arrivals per second (Poisson). 0 disables inter-arrival waits",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    random.seed(args.seed)
    base = args.url.rstrip("/")

    # Discover content paths from the public page tree if we can.
    paths: list[str] = []
    try:
        r = httpx.get(urljoin(base, "/_api/pages"), timeout=10.0)
        if r.status_code == 200:
            paths = [page["path"] for page in r.json() if page.get("has_content")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not fetch page list: %s", exc)
    if not paths:
        paths = ["/"]

    logger.info(
        "Generating fake traffic against %s (%d content paths, %d browsers, %d crawlers)",
        base,
        len(paths),
        args.browsers,
        args.crawlers,
    )

    results: list[dict[str, Any]] = []

    for i in range(args.browsers):
        if i > 0:
            wait = _poisson_wait(args.arrival_rate)
            logger.debug("waiting %.2fs before next browser session", wait)
            time.sleep(wait)
        profile = random.choice(BROWSER_PROFILES)
        fake_ip = _source_ip(i)
        logger.info(
            "[%d/%d] browser session: %s (ip=%s)",
            i + 1,
            args.browsers,
            profile.name,
            fake_ip,
        )
        result = _run_browser_session(
            base,
            paths,
            profile,
            i,
            args.max_clicks,
            (args.stay[0], args.stay[1]),
            args.headless,
            fake_ip,
        )
        results.append(result)
        logger.debug("  trail: %s", result.get("trail", []))

    for i in range(args.crawlers):
        if i > 0:
            wait = _poisson_wait(args.arrival_rate)
            logger.debug("waiting %.2fs before next crawler hit", wait)
            time.sleep(wait)
        profile_index = i % len(CRAWLER_PROFILES)
        profile = CRAWLER_PROFILES[profile_index]
        fake_ip = _source_ip(i)
        logger.info(
            "[%d/%d] crawler hit: %s (ip=%s)",
            i + 1,
            args.crawlers,
            profile.name,
            fake_ip,
        )
        result = _run_crawler_hit(base, paths, profile, profile_index, i)
        results.append(result)

    ok = sum(1 for r in results if "error" not in r)
    logger.info("Done: %d/%d requests succeeded.", ok, len(results))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
