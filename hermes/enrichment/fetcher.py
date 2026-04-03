import logging
import requests
from dataclasses import dataclass

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cf_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    logger.warning("curl-cffi not installed — CF bypass fetch path disabled")

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    logger.warning("Playwright not installed — Playwright fetch path disabled")


@dataclass
class FetchResult:
    text: str | None
    screenshot_b64: str | None
    method: str  # "http" | "cf_get" | "playwright_text" | "playwright_screenshot"

_MIN_CONTENT_LENGTH = 300

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_STRIP_TAGS = {"nav", "footer", "script", "style", "header"}
_STRIP_CLASS_PATTERNS = ("cookie", "related", "similar")
_CONTENT_SELECTORS = [
    "main",
    "[class*='detail']",
    "[class*='listing']",
    "[class*='property']",
    "article",
]


def _extract_content(soup: BeautifulSoup) -> str:
    """Extract main content from a detail page, stripping boilerplate."""
    for tag in _STRIP_TAGS:
        for el in soup.find_all(tag):
            el.decompose()

    for pattern in _STRIP_CLASS_PATTERNS:
        for el in soup.find_all(class_=lambda c: c and pattern in c):
            el.decompose()

    for selector in _CONTENT_SELECTORS:
        hit = soup.select_one(selector)
        if hit:
            text = hit.get_text(separator="\n", strip=True)
            if len(text) >= _MIN_CONTENT_LENGTH:
                return text

    body = soup.find("body")
    if body:
        return body.get_text(separator="\n", strip=True)

    return soup.get_text(separator="\n", strip=True)


def _fetch_cf(url: str) -> FetchResult:
    """curl-cffi GET with Chrome TLS fingerprint — bypasses Cloudflare Bot Management."""
    if not HAS_CURL_CFFI:
        raise RuntimeError("curl-cffi not available")
    r = cf_requests.get(url, impersonate="chrome124", timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = _extract_content(soup)
    return FetchResult(text=text, screenshot_b64=None, method="cf_get")


def _fetch_http(url: str) -> FetchResult:
    """Plain HTTP GET + BeautifulSoup extraction."""
    resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    text = _extract_content(soup)
    return FetchResult(text=text, screenshot_b64=None, method="http")


def _fetch_playwright(url: str) -> FetchResult:
    """Playwright headless Chromium with text extraction, screenshot fallback."""
    if not HAS_PLAYWRIGHT:
        return FetchResult(text=None, screenshot_b64=None, method="playwright_text")

    pw = sync_playwright().start()
    browser = None
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=_USER_AGENT)
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2000)

        text = page.inner_text("body")
        if text and len(text) >= _MIN_CONTENT_LENGTH:
            return FetchResult(text=text, screenshot_b64=None, method="playwright_text")

        import base64
        screenshot_bytes = page.screenshot(full_page=True)
        b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        return FetchResult(text=None, screenshot_b64=b64, method="playwright_screenshot")
    finally:
        if browser:
            browser.close()
        pw.stop()


def fetch_detail_page(url: str, agency: str, method: str = "http") -> FetchResult:
    """Tiered fetch routed by detail_fetch_method from the targets table.

    method values: 'cf' | 'playwright' | 'http' (default)
    """
    if method == "cf":
        logger.debug("fetch_detail_page: agency '%s' → CF bypass for %s", agency, url)
        try:
            result = _fetch_cf(url)
            if result.text and len(result.text) >= _MIN_CONTENT_LENGTH:
                return result
        except Exception as e:
            logger.info("fetch_detail_page: CF fetch failed for %s: %r — falling back to Playwright", url, e)
        return _fetch_playwright(url)

    if method == "playwright":
        logger.debug("fetch_detail_page: agency '%s' → direct Playwright for %s", agency, url)
        return _fetch_playwright(url)

    # Default: plain HTTP with Playwright fallback
    try:
        result = _fetch_http(url)
        if result.text and len(result.text) >= _MIN_CONTENT_LENGTH:
            logger.debug("fetch_detail_page: HTTP ok (%d chars) for %s", len(result.text), url)
            return result
    except requests.RequestException as e:
        logger.info("fetch_detail_page: HTTP failed for %s: %r", url, e)

    logger.debug("fetch_detail_page: HTTP content too short, trying Playwright for %s", url)
    return _fetch_playwright(url)
