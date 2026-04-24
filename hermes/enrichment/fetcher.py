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


def _fetch_athomevastgoed_detail(url: str) -> FetchResult:
    """Agency-specific extractor for At Home Vastgoed detail pages.

    These pages are a Vue SPA: the server returns a 1MB+ HTML skeleton
    where virtually all visible content is populated by JS from inline
    Vuex data — ``soup.get_text()`` yields only the navigation (~136
    chars). The generic ``_extract_content`` path therefore returns
    nothing and falls through to Playwright (which isn't in the image).

    Instead we pull from the two places the data actually lives:
      (a) ``description_trans`` JSON (inline script) for the full
          English description, price, area, rooms, available-from.
      (b) ``.appointments-widget`` HTML block (which *is* in the DOM)
          for viewing-slot status.

    This mirrors the index-page strategy in ``HomeResults.parse_athomevastgoed``
    and is far more robust than pretending the page is normal HTML.
    """
    if not HAS_CURL_CFFI:
        raise RuntimeError("curl-cffi not available for athomevastgoed")

    r = cf_requests.get(
        url,
        impersonate="chrome124",
        timeout=25,
        headers={
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    r.raise_for_status()
    html = r.text

    import re
    import json as _json
    import html as _html_mod

    parts: list[str] = [f"URL: {url}"]

    # --- English description from the inline Vuex payload ---
    # Shape: ..."description_trans":{"en":"<p>...</p><p>...</p>","nl":"..."}...
    # Greedy match fails (description contains escaped quotes); walk until we
    # hit the end of the English value by tracking escape state.
    marker = '"description_trans":{"en":"'
    idx = html.find(marker)
    if idx >= 0:
        start = idx + len(marker)
        i = start
        escape = False
        while i < len(html):
            c = html[i]
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                break
            i += 1
        raw = html[start:i]
        # Unescape JSON escapes and strip HTML
        try:
            decoded = _json.loads('"' + raw + '"')
        except Exception:
            decoded = raw
        decoded = re.sub(r"<br\s*/?>", "\n", decoded, flags=re.I)
        decoded = re.sub(r"</p\s*>", "\n\n", decoded, flags=re.I)
        decoded = re.sub(r"<[^>]+>", " ", decoded)
        decoded = _html_mod.unescape(decoded).strip()
        if decoded:
            parts.append(f"Description:\n{decoded}")

    # --- Structured facts from the inline property JSON ---
    # Keys we care about, each matched individually so partial malformations
    # don't nuke the whole block.
    # Two shapes: quoted string value (may contain commas, e.g. "1185,00")
    # or unquoted numeric/boolean (e.g. 65, true, null).
    def _grab(key: str) -> str | None:
        # Quoted value, allow commas inside.
        m = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', html)
        if m:
            return m.group(1).strip() or None
        # Unquoted value up to next comma/brace.
        m = re.search(rf'"{re.escape(key)}"\s*:\s*([^,}}\s]+)', html)
        if m:
            val = m.group(1).strip()
            return val if val and val != "null" else None
        return None

    facts: list[str] = []
    for label, key in [
        ("Street", "street"),
        ("Postcode", "postcode"),
        ("Price (€/month)", "ah_price"),
        ("Area (m²)", "area"),
        ("Bedrooms", "no_bedrooms"),
        ("Available from", "available_on"),
    ]:
        val = _grab(key)
        if val:
            facts.append(f"{label}: {val}")
    if facts:
        parts.append("Listing facts:\n" + "\n".join(facts))

    # --- Appointments widget (real DOM; a small, well-formed block) ---
    # Simpler than regex-matching nested divs: split the page on the item
    # marker and parse each chunk until we hit the first widget-level close.
    item_marker = '<div class="appointments-widget__item">'
    first_widget_idx = html.find('<div class="appointments-widget">')
    if first_widget_idx >= 0:
        widget_region = html[first_widget_idx : first_widget_idx + 50000]
        chunks = widget_region.split(item_marker)[1:]  # drop the header
        appt_lines: list[str] = []
        for chunk in chunks:
            # Each item ends at the item's own closing </div>; stop before
            # anything that belongs to the next item or the widget footer.
            end_idx = chunk.find(item_marker)
            if end_idx >= 0:
                chunk = chunk[:end_idx]
            # Also cap at the reservelist widget if we overran.
            for stop in ['<div class="viewing-widget"', '<!--']:
                si = chunk.find(stop)
                if si >= 0:
                    chunk = chunk[:si]

            dt = re.search(r"<dd[^>]*>\s*<strong>([^<]+)(?:<br[^>]*>\s*([^<]+))?", chunk)
            status_m = re.search(
                r'<span class="text-(red|green|gray)[^"]*"[^>]*><strong>([^<]+)</strong>',
                chunk,
            )
            date = dt.group(1).strip() if dt else "?"
            time_s = (dt.group(2) or "").strip() if dt else ""
            status = status_m.group(2).strip() if status_m else "Open"
            appt_lines.append(f"  - {date} {time_s} [{status}]")
        if appt_lines:
            parts.append("Appointments:\n" + "\n".join(appt_lines))

    text = "\n\n".join(parts)
    logger.debug("athomevastgoed detail extractor produced %d chars", len(text))
    return FetchResult(text=text, screenshot_b64=None, method="athomevastgoed_custom")


def fetch_detail_page(url: str, agency: str, method: str = "http") -> FetchResult:
    """Tiered fetch routed by detail_fetch_method from the targets table.

    method values: 'cf' | 'playwright' | 'http' (default)

    Some agencies are Vue/React SPAs where the page text is effectively empty
    at the DOM level (content is materialized from inline data by JS). For
    those, we dispatch to a site-specific extractor that knows where to
    look — much cheaper than headless Chromium.
    """
    if agency == "athomevastgoed":
        logger.debug("fetch_detail_page: using athomevastgoed-specific extractor for %s", url)
        try:
            return _fetch_athomevastgoed_detail(url)
        except Exception as e:
            logger.info("fetch_detail_page: athomevastgoed extractor failed: %r — falling back to generic path", e)
            # Fall through to the generic tiered logic so a bug here doesn't
            # take analysis offline for the agency.

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
