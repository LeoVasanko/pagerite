#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "httpx>=0.28.1",
#     "playwright>=1.45.0",
# ]
# ///
"""Generate fake browser visits, crawler hits, and abuse scans for a Pagerite site.

Browser sessions (ordinary users) come from realistic residential IPv4 and IPv6
addresses and stay mostly stable; an IPv6 host part may rotate once mid-session,
and an IPv4 session may switch to another residential address.  Crawler hits come
from datacenter IPs, with each crawler profile paired to a matching provider IP
when possible.  Abuse scanners fire bursts of vulnerability probes from pinned
datacenter IPs.

Run against a local dev server, e.g.:

    uv run scripts/fake_traffic.py http://localhost:3200

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
from urllib.parse import urlencode, urljoin, urlparse

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
    ip: str


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
        "66.249.64.66",  # US, Google
    ),
    CrawlerProfile(
        "bingbot",
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm) Chrome/128.0.0.0 Safari/537.36",
        "40.77.167.0",  # US, Microsoft
    ),
    CrawlerProfile(
        "duckduckbot",
        "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)",
        "95.217.0.1",  # Germany, Hetzner VPS
    ),
    CrawlerProfile(
        "curl",
        "curl/8.5.0",
        "139.162.0.1",  # Singapore, Linode VPS
    ),
]

# Residential IPv4 addresses and IPv6 /64 prefixes used for ordinary browser
# sessions.  IPv6 entries keep the network part stable and randomise only the
# host part; the host may rotate once mid-session.
RESIDENTIAL_SOURCE_IPS: list[str] = [
    # Residential IPv4
    "91.154.140.209",  # Finland, Elisa
    "84.143.145.207",  # Germany, Deutsche Telekom
    "220.165.255.254",  # China, Chinanet / China Telecom
    "84.235.83.162",  # Saudi Arabia, SaudiNet / STC
    # Residential IPv6 /64 prefixes
    "2a02:8109:ac82:6f0c::/64",  # Germany, Deutsche Telekom
    "240e:45d:1e60:5b0::/64",  # China, China Telecom
    "2409:8904:6720:4123::/64",  # China, China Unicom
]

# Concrete datacenter IPs used for abuse scanner bursts.  They stay pinned for
# the whole scan burst.
# Index 0 randomises its UA per request, index 1 uses a fixed browser UA,
# and index 2 uses a fixed crawler UA.
ABUSE_SOURCE_IPS: list[str] = [
    "45.63.0.12",  # US, Vultr VPS
    "138.197.0.89",  # US, DigitalOcean / Cloudways
    "2a01:4f8:0:2::1234",  # Germany, Hetzner VPS
]

# Paths commonly probed by attackers looking for exposed config, admin panels,
# version control, credentials, backups, or debug endpoints.
SUSPICIOUS_PATHS: list[str] = [
    "/.env",
    "/env",
    "/.env.local",
    "/env.development",
    "/config",
    "/config.json",
    "/config.yaml",
    "/config.yml",
    "/configuration.json",
    "/configuration.yaml",
    "/configuration.yml",
    "/settings.json",
    "/settings.yaml",
    "/settings.yml",
    "/app.config",
    "/appsettings.json",
    "/appsettings.Development.json",
    "/credentials",
    "/credentials.json",
    "/secrets",
    "/secrets.json",
    "/.aws/credentials",
    "/.ssh/id_rsa",
    "/id_rsa",
    "/id_rsa.pub",
    "/known_hosts",
    "/sftp-config.json",
    "/admin",
    "/administrator",
    "/adminer.php",
    "/login",
    "/signin",
    "/auth/login",
    "/api/login",
    "/api/.env",
    "/api/config",
    "/api/v1/config",
    "/api/v2/config",
    "/webhook",
    "/webhooks",
    "/callback",
    "/proxy",
    "/image",
    "/images",
    "/preview",
    "/download",
    "/downloads",
    "/log",
    "/logs",
    "/debug",
    "/trace",
    "/phpinfo.php",
    "/info.php",
    "/phpmyadmin",
    "/pma",
    "/myadmin",
    "/phpMyAdmin",
    "/wp-admin",
    "/wp-login.php",
    "/wp-config.php",
    "/xmlrpc.php",
    "/wp-json/wp/v2/users",
    "/.git/config",
    "/.git/HEAD",
    "/git/config",
    "/swagger-ui.html",
    "/v2/api-docs",
    "/actuator/env",
    "/actuator/health",
    "/actuator/configprops",
    "/server-status",
    "/.htaccess",
    "/web.config",
    "/package.json",
    "/composer.json",
    "/vendor/autoload.php",
    "/docker-compose.yml",
    "/Dockerfile",
    "/manage",
    "/console",
    "/manager",
    "/manager/html",
    "/metrics",
    "/prometheus",
    "/healthz",
    "/_api",
    "/api",
    "/api/v1/",
    "/api/v2/",
    "/graphql",
    "/query",
    "/feed",
    "/rss",
    "/_debug",
    "/test",
    "/testing",
    "/tmp",
    "/temp",
    "/backup",
    "/backups",
    "/dump",
    "/dumps",
    "/sql",
    "/db",
    "/database",
    "/dump.sql",
    "/backup.sql",
    "/db.sql",
    "/backup.zip",
    "/backup.tar.gz",
    "/site.zip",
    "/site.tar.gz",
    "/source.zip",
    "/src.zip",
    "/upload",
    "/uploads",
    "/import",
    "/export",
    "/token",
    "/tokens",
    "/oauth",
    "/oauth2",
    "/openid",
    "/jwks",
    "/keys",
    "/key",
    "/private",
    "/public",
]

# Realistic external referers.  Most sessions arrive with a generic referer;
# a subset carries matching UTM tags on the landing URL.
PLAIN_REFERRERS: list[str] = [
    "https://example.com/",
    "https://somedomain.com/",
    "https://another-site.org/",
    "https://friend-site.net/",
]

# (referer origin, utm parameter dict) pairs used for tagged traffic.
TAGGED_REFERRERS: list[tuple[str, dict[str, str]]] = [
    ("https://chatgpt.com/", {"utm_source": "chatgpt.com"}),
    ("https://www.google.com/", {"utm_source": "google", "utm_medium": "organic"}),
    ("https://twitter.com/", {"utm_source": "twitter", "utm_medium": "social"}),
    ("https://www.linkedin.com/", {"utm_source": "linkedin", "utm_medium": "social"}),
    ("https://github.com/", {"utm_source": "github", "utm_medium": "referral"}),
    (
        "https://news.ycombinator.com/",
        {"utm_source": "hackernews", "utm_medium": "referral"},
    ),
    ("https://www.reddit.com/", {"utm_source": "reddit", "utm_medium": "social"}),
    ("https://medium.com/", {"utm_source": "medium", "utm_medium": "referral"}),
    (
        "https://www.producthunt.com/",
        {"utm_source": "producthunt", "utm_medium": "referral"},
    ),
]

# Fraction of referered sessions that also carry UTM tags.
UTM_RATE = 0.25

# Innocent-looking paths that do not exist on a Pagerite site.  Hitting many of
# these from a single IP is itself a telltale of a spray-and-pray scanner.
NORMAL_404_PATHS: list[str] = [
    "/about",
    "/about-us",
    "/services",
    "/products",
    "/contact",
    "/contact-us",
    "/team",
    "/careers",
    "/jobs",
    "/pricing",
    "/features",
    "/demo",
    "/trial",
    "/docs",
    "/documentation",
    "/api-docs",
    "/support",
    "/help",
    "/faq",
    "/knowledge-base",
    "/terms",
    "/terms-of-service",
    "/privacy",
    "/privacy-policy",
    "/legal",
    "/blog",
    "/news",
    "/articles",
    "/press",
    "/events",
    "/webinars",
    "/podcast",
    "/videos",
    "/resources",
    "/whitepapers",
    "/case-studies",
    "/customers",
    "/clients",
    "/testimonials",
    "/reviews",
    "/partners",
    "/integrations",
    "/api-reference",
    "/developers",
    "/status",
    "/security",
    "/trust",
    "/compliance",
    "/gdpr",
    "/ccpa",
    "/sitemap",
    "/archive",
    "/tags",
    "/categories",
    "/search",
    "/users",
    "/accounts",
    "/dashboard",
    "/profile",
    "/settings",
    "/preferences",
    "/notifications",
    "/messages",
    "/inbox",
    "/calendar",
    "/reports",
    "/analytics",
    "/billing",
    "/invoice",
    "/orders",
    "/cart",
    "/checkout",
    "/store",
    "/shop",
    "/home",
    "/main",
    "/start",
    "/welcome",
    "/intro",
    "/overview",
    "/summary",
    "/portfolio",
    "/projects",
    "/work",
    "/solutions",
]

ABUSE_USER_AGENTS: list[str] = [
    # Desktop browsers
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    # Well-known crawlers / bots
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; "
    "+http://www.google.com/bot.html) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; "
    "+http://www.bing.com/bingbot.htm) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (compatible; DuckDuckBot/1.1; +http://duckduckgo.com/duckduckbot.html)",
    "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)",
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
    "Mozilla/5.0 (compatible; DotBot/1.2; +https://opensiteexplorer.org/dotbot; help@moz.com)",
    "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    # Social / service fetchers
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Twitterbot/1.0",
    "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "WhatsApp/2.23.20.0",
    # Command-line / library clients
    "curl/8.5.0",
    "Wget/1.21.4 (linux-gnu)",
    "python-requests/2.32.3",
    "Go-http-client/1.1",
    "Node.js/20.5.1",
]


def _random_ipv6_host(prefix: str) -> str:
    """Return a concrete address within an IPv6 /64 prefix.

    The host part is generated randomly, mimicking a fresh OS privacy address.
    The input prefix must end in ``::/64`` (e.g. ``2a02:8109:ac82:6f0c::/64``).
    """
    if "/" not in prefix:
        return prefix
    base, mask = prefix.split("/")
    if mask != "64":
        raise ValueError(f"only /64 IPv6 prefixes are supported, got {prefix!r}")
    if base.endswith("::"):
        base = base[:-2]
    host = ":".join(f"{random.randint(0, 0xFFFF):04x}" for _ in range(4))
    return f"{base}:{host}"


def _concretize_ip(entry: str) -> str:
    """Return a concrete IP address; randomise the host part for IPv6 /64 prefixes."""
    if ":" in entry and "/" in entry:
        return _random_ipv6_host(entry)
    return entry


class _SessionIP:
    """Stable IP for a browser session, with one optional mid-session rotation.

    IPv6 prefixes get a fresh random host part; IPv4 addresses are swapped for
    another address from the residential pool.
    """

    def __init__(self, entry: str, pool: Sequence[str]):
        self.entry = entry
        self.pool = pool
        self._value = _concretize_ip(entry)

    def current(self) -> str:
        return self._value

    def rotate(self) -> None:
        if ":" in self.entry and "/" in self.entry:
            self._value = _random_ipv6_host(self.entry)
            return
        # IPv4: switch to another IPv4 address from the residential pool.
        for _ in range(20):
            candidate_entry = random.choice(self.pool)
            if ":" in candidate_entry and "/" in candidate_entry:
                continue
            candidate = _concretize_ip(candidate_entry)
            if candidate != self._value:
                self._value = candidate
                return


def _sleep(base: float, jitter: float) -> None:
    time.sleep(max(0.0, base + random.uniform(-jitter, jitter)))


def _normalize_url(url: str) -> str:
    """Return a usable base URL, adding missing scheme/host/port parts.

    - bare ``:PORT`` becomes ``http://localhost:PORT``
    - missing scheme becomes ``http://``
    - otherwise returned as-is

    Raises ``ValueError`` when the result is not a valid http(s) URL.
    """
    raw = url.strip()
    if not raw:
        raise ValueError("empty URL")
    if raw.startswith(":"):
        raw = f"http://localhost{raw}"
    elif raw.isdigit():
        raw = f"http://localhost:{raw}"
    elif not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid URL: {url!r}")
    return raw


def _poisson_wait(rate: float) -> float:
    """Return an exponential inter-arrival time for the given Poisson rate."""
    if rate <= 0:
        return 0.0
    return random.expovariate(rate)


def _collect_links(page: Any, include_external: bool = False) -> list[dict[str, Any]]:
    """Return links from the current page, excluding the current page.

    Internal links stay on the site; external links are real https URLs found
    in the page content and are marked with ``external: true``.
    """
    return page.evaluate(
        """(includeExternal) => {
            const loc = new URL(location.href);
            const out = [];
            for (const a of document.querySelectorAll('a[href]')) {
                try {
                    const u = new URL(a.href);
                    const rect = a.getBoundingClientRect();
                    const item = {
                        href: a.href,
                        text: (a.innerText || a.title || '').trim().slice(0, 60),
                        visible: !!(rect.width && rect.height && rect.top < window.innerHeight && rect.bottom > 0),
                    };
                    if (u.origin === loc.origin
                            && !u.pathname.startsWith('/_')
                            && !u.pathname.startsWith('/auth')
                            && u.pathname !== '/favicon.ico'
                            && u.pathname !== loc.pathname) {
                        out.push(item);
                    } else if (includeExternal && u.protocol === 'https:' && u.origin !== loc.origin) {
                        out.push({ ...item, external: true });
                    }
                } catch { /* ignore malformed hrefs */ }
            }
            return out;
        }""",
        include_external,
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
    ip_entry: str,
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    MAX_CLICKS = 6
    STAY = (2.0, 6.0)
    HEADLESS = True
    REFERER_RATE = 0.75
    INCLUDE_EXTERNAL = True

    ip_provider = _SessionIP(ip_entry, RESIDENTIAL_SOURCE_IPS)
    ips_used: list[str] = [ip_provider.current()]
    trail: list[str] = []
    start_time = datetime.now(UTC)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=HEADLESS,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            extra_headers = {
                "X-Forwarded-For": ip_provider.current(),
                "Accept-Language": profile.accept_language,
            }
            # Most sessions arrive from an external origin; some are direct.
            # A subset of referered sessions carries realistic UTM tags on the
            # landing URL; the referer origin is paired with the UTM source.
            tagged: dict[str, str] = {}
            if random.random() < REFERER_RATE:
                if random.random() < UTM_RATE:
                    referer, tagged = random.choice(TAGGED_REFERRERS)
                else:
                    referer = random.choice(PLAIN_REFERRERS)
                extra_headers["Referer"] = referer
            context = browser.new_context(
                user_agent=profile.user_agent,
                viewport={"width": profile.viewport[0], "height": profile.viewport[1]},
                extra_http_headers=extra_headers,
            )
            page = context.new_page()

            # Update X-Forwarded-For per request; the value stays stable unless we
            # explicitly rotate it once mid-session.
            def _route_handler(route, request):
                headers = dict(request.headers)
                headers["X-Forwarded-For"] = ip_provider.current()
                ips_used.append(headers["X-Forwarded-For"])
                route.continue_(headers=headers)

            page.route("**/*", _route_handler)

            # Pick one point during the session to emulate an IP rotation.
            rotate_at = random.randint(0, MAX_CLICKS - 1) if MAX_CLICKS > 0 else -1

            entry = random.choice(paths) if paths else "/"
            landing = urljoin(base, entry)
            if tagged:
                sep = "&" if "?" in landing else "?"
                landing += sep + urlencode(tagged)
            page.goto(landing, wait_until="networkidle")
            trail.append(page.url)

            for click_idx in range(MAX_CLICKS):
                _sleep(random.uniform(*STAY) / 2, 0.3)
                if click_idx == rotate_at:
                    ip_provider.rotate()
                    ips_used.append(ip_provider.current())
                    logger.debug("rotated session IP to %s", ip_provider.current())
                links = _collect_links(page, INCLUDE_EXTERNAL)
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
                if link.get("external"):
                    # Outbound navigation: the analytics exit ping is already
                    # in flight. Record the external URL and end the session.
                    trail.append(page.url)
                    _sleep(0.5, 0.2)
                    break
                page.wait_for_load_state("networkidle")
                trail.append(page.url)
                _sleep(random.uniform(*STAY), 0.5)

            browser.close()

        return {
            "profile": profile.name,
            "entry": entry,
            "ip": ips_used[0],
            "ips_seen": len(set(ips_used)),
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
) -> dict[str, Any]:
    path = random.choice(paths) if paths else "/"
    url = urljoin(base, path)
    fake_ip = profile.ip
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


def _abuse_ua() -> str:
    """Return a randomized, syntactically valid user agent for an abuse scan."""
    return random.choice(ABUSE_USER_AGENTS)


def _run_abuse_scanner(base: str, ip_index: int) -> dict[str, Any]:
    """Fire a burst of vulnerability probes from a single fake IP.

    Scanner 0 randomises its user agent every request, scanner 1 uses a fixed
    browser UA, and scanner 2 uses a fixed crawler UA.
    """
    ip_entry = ABUSE_SOURCE_IPS[ip_index % len(ABUSE_SOURCE_IPS)]
    if ":" in ip_entry and "/" in ip_entry:
        fake_ip = _random_ipv6_host(ip_entry)
    else:
        fake_ip = ip_entry

    MIN_HITS = 15
    MAX_HITS = 25
    total_hits = random.randint(MIN_HITS, MAX_HITS)

    # Ensure the burst contains both telltales: suspicious paths and more
    # than ten normal-looking 404 paths.
    suspicious_count = max(5, total_hits // 3)
    normal_count = total_hits - suspicious_count
    if normal_count < 11:
        normal_count = 11
        suspicious_count = max(3, total_hits - normal_count)

    paths = random.choices(SUSPICIOUS_PATHS, k=suspicious_count) + random.choices(
        NORMAL_404_PATHS, k=normal_count
    )
    random.shuffle(paths)

    ua_mode = ip_index % 3
    if ua_mode == 0:
        get_ua = _abuse_ua
    elif ua_mode == 1:

        def get_ua() -> str:
            return BROWSER_PROFILES[0].user_agent
    else:

        def get_ua() -> str:
            return CRAWLER_PROFILES[0].user_agent

    scan_results: list[dict[str, Any]] = []
    with httpx.Client(follow_redirects=True, timeout=15.0) as client:
        for path in paths:
            headers = {
                "User-Agent": get_ua(),
                "X-Forwarded-For": fake_ip,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": random.choice(
                    ["en-US,en;q=0.9", "en-GB,en;q=0.8", "en;q=0.7"]
                ),
            }
            try:
                r = client.get(urljoin(base, path), headers=headers)
                scan_results.append(
                    {"path": path, "status": r.status_code, "ua": headers["User-Agent"]}
                )
            except Exception as exc:  # noqa: BLE001
                scan_results.append({"path": path, "error": str(exc)})
            _sleep(0.15, 0.1)

    return {
        "scanner": ip_index + 1,
        "ip": fake_ip,
        "hits": len(scan_results),
        "results": scan_results,
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate fake traffic for a Pagerite site.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "url",
        nargs="?",
        default="http://localhost:8200",
        help="Base URL of the Pagerite site (default: http://localhost:8200). "
        "A bare :PORT or PORT is treated as http://localhost:PORT; a "
        "missing scheme defaults to http://.",
    )
    parser.add_argument(
        "-t",
        "--duration",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="Rough maximum time to generate traffic (0 runs one preset batch)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    try:
        base = _normalize_url(args.url).rstrip("/")
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

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
        "Generating fake traffic against %s (%d content paths, duration=%ss)",
        base,
        len(paths),
        args.duration,
    )

    results: list[dict[str, Any]] = []
    arrival_rate = 1.0

    def _wait() -> None:
        wait = _poisson_wait(arrival_rate)
        logger.debug("waiting %.2fs before next session", wait)
        time.sleep(wait)

    if args.duration <= 0:
        # One preset batch.
        for i in range(5):
            if i > 0:
                _wait()
            profile = random.choice(BROWSER_PROFILES)
            ip_entry = random.choice(RESIDENTIAL_SOURCE_IPS)
            logger.info(
                "browser session: %s (ip=%s)",
                profile.name,
                _concretize_ip(ip_entry),
            )
            result = _run_browser_session(base, paths, profile, i, ip_entry)
            results.append(result)
            logger.debug("  trail: %s", result.get("trail", []))

        for i in range(10):
            if i > 0:
                _wait()
            profile = random.choice(CRAWLER_PROFILES)
            logger.info(
                "crawler hit: %s (ip=%s)",
                profile.name,
                profile.ip,
            )
            result = _run_crawler_hit(base, paths, profile)
            results.append(result)

        for i in range(3):
            if i > 0:
                _wait()
            ip_entry = ABUSE_SOURCE_IPS[i % len(ABUSE_SOURCE_IPS)]
            logger.info("abuse scanner: %s", ip_entry)
            result = _run_abuse_scanner(base, i)
            results.append(result)
            logger.debug(
                "  hits: %s", [r.get("path") for r in result.get("results", [])]
            )
    else:
        deadline = time.time() + args.duration
        session_index = 0
        while time.time() < deadline:
            if session_index > 0:
                _wait()
            phase = session_index % 3
            if phase == 0:
                profile = random.choice(BROWSER_PROFILES)
                ip_entry = random.choice(RESIDENTIAL_SOURCE_IPS)
                logger.info(
                    "browser session: %s (ip=%s)",
                    profile.name,
                    _concretize_ip(ip_entry),
                )
                result = _run_browser_session(
                    base, paths, profile, session_index, ip_entry
                )
                logger.debug("  trail: %s", result.get("trail", []))
            elif phase == 1:
                profile = random.choice(CRAWLER_PROFILES)
                logger.info(
                    "crawler hit: %s (ip=%s)",
                    profile.name,
                    profile.ip,
                )
                result = _run_crawler_hit(base, paths, profile)
            else:
                ip_entry = ABUSE_SOURCE_IPS[session_index % len(ABUSE_SOURCE_IPS)]
                logger.info("abuse scanner: %s", ip_entry)
                result = _run_abuse_scanner(base, session_index // 3)
                logger.debug(
                    "  hits: %s",
                    [r.get("path") for r in result.get("results", [])],
                )
            results.append(result)
            session_index += 1

    ok = sum(1 for r in results if "error" not in r)
    logger.info("Done: %d/%d requests succeeded.", ok, len(results))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
