# Agent 2: Detail Page Fetcher + Batch Analyzer

Read `@plans/context.md` first for full project context.

## Prerequisites

Agent 1 must be complete. Verify these files exist before starting:
- `misc/sql/enrichment_schema.sql`
- `hestia/enrichment/__init__.py`
- `hestia/enrichment/profile.py`
- `hestia/enrichment/queue.py`
- `hestia/enrichment/costs.py`

Read all of them to understand the interfaces you depend on.

## Your Job

1. `hestia/enrichment/fetcher.py` — detail page content extraction
2. `hestia/enrichment/analyzer.py` — the scheduled batch analysis job
3. Add `anthropic` to `hestia/requirements.txt`

## Files to Read First

- `hestia/enrichment/profile.py` — you call `get_profiles_with_enrichment()`, `get_profile_by_id()`, `build_system_prompt()`
- `hestia/enrichment/queue.py` — you call `drain_pending()`, `mark_done()`, `mark_failed()`, `update_page_text()`
- `hestia/enrichment/costs.py` — you call `log_usage()`, `check_daily_budget()`
- `hestia/hestia_utils/db.py` — for DB write patterns when storing enrichment_results
- `hestia/hestia_utils/meta.py` — for `BOT`, `escape_markdownv2()`, emoji constants
- `hestia/hestia_utils/parser.py` — just the `Home` class (lines 11-84), to understand the data shape
- `hestia/requirements.txt` — to add `anthropic`

## Files to Create

### 1. `hestia/enrichment/fetcher.py`

```python
import logging
import requests
from dataclasses import dataclass
from bs4 import BeautifulSoup

@dataclass
class FetchResult:
    text: str | None
    screenshot_b64: str | None
    method: str   # "http" | "playwright_text" | "playwright_screenshot"

# Sites known to need Playwright for their DETAIL pages (not search results).
# The search results for these sites work fine with HTTP (existing parsers handle that).
# This list is for the individual listing detail pages only.
PLAYWRIGHT_DETAIL_SITES: set[str] = set()
# Populated as you discover sites whose detail pages are JS-rendered.
# Start empty — most detail pages work with HTTP.

def _extract_content(soup: BeautifulSoup) -> str:
    """Extract main content from a detail page.
    Try selectors in order: <main>, [class*='detail'], [class*='listing'],
    [class*='property'], article, then fall back to body.
    Strip: nav, footer, script, style, [class*='cookie'], [class*='related'],
    [class*='similar'], header."""

def _fetch_http(url: str) -> FetchResult:
    """Plain HTTP GET + BeautifulSoup extraction.
    Timeout: 15 seconds. User-Agent: a real browser string.
    Return FetchResult with method='http'."""

def _fetch_playwright(url: str) -> FetchResult:
    """Playwright headless Chromium. Wait 2 seconds after load.
    First try page.inner_text('body') — if >= 300 chars, return as text.
    Otherwise take full-page screenshot as base64.
    ALWAYS close browser in finally block.
    Return FetchResult with appropriate method."""

def fetch_detail_page(url: str, agency: str) -> FetchResult:
    """Tiered fetch:
    1. If agency in PLAYWRIGHT_DETAIL_SITES, go straight to Playwright.
    2. Otherwise try HTTP first.
    3. If HTTP text < 300 chars, try Playwright.
    4. If Playwright text also < 300 chars, take screenshot.
    Log the method used at WARNING level."""
```

Important:
- Playwright is an **optional** dependency. Wrap the import in a try/except. If Playwright is not installed, log a warning and skip the Playwright path.
- Set a reasonable User-Agent header for HTTP requests.
- The 300-char threshold is for the **extracted content**, not raw HTML.

### 2. `hestia/enrichment/analyzer.py`

This is the scheduled batch job entry point — called by cron, not imported by other modules.

```python
import json
import logging
import anthropic
from datetime import datetime

from hestia_utils.db import get_connection, _write, fetch_one
import hestia_utils.meta as meta

from hestia.enrichment.profile import get_profile_by_id, build_system_prompt
from hestia.enrichment.queue import drain_pending, mark_done, mark_failed, update_page_text
from hestia.enrichment.fetcher import fetch_detail_page
from hestia.enrichment.costs import log_usage, check_daily_budget

ANALYSIS_MODEL = "claude-haiku-4-5-20251001"
LETTER_MODEL = "claude-sonnet-4-20250514"
MAX_BATCH_SIZE = 20

def _build_analysis_prompt(listings: list[dict]) -> str:
    """Build the user prompt with the JSON array of listings.
    Each listing: {"index": i, "url": url, "text": page_text}
    Use the exact prompt format from context.md."""

def _parse_claude_response(response_text: str) -> list[dict]:
    """Parse Claude's JSON response. Handle:
    - Accidental markdown fences (strip ```json and ```)
    - Truncated JSON (return partial results + log warning)
    - Complete garbage (return empty list + log error)
    Never raise — always return a list."""

def _store_verdict(queue_item: dict, verdict: dict) -> None:
    """INSERT into hestia.enrichment_results.
    Map verdict fields to table columns.
    listing_json = verdict['listing'] as JSONB.
    income_check = verdict['income_check'] as JSONB."""

async def _send_enriched_message(telegram_id: str, verdict: dict, url: str) -> None:
    """Format and send enriched Telegram message.
    Use MarkdownV2. Include inline keyboard with buttons:
    - View Listing → url
    - Letter NL → callback_data='letter_nl:{result_id}'
    - Letter EN → callback_data='letter_en:{result_id}'
    - Apply → application_url from verdict, or url as fallback

    For score < 5, don't send individual messages.
    Collect them and send one grouped summary at the end."""

async def _auto_generate_letters(profile: dict, verdict: dict, result_id: str) -> None:
    """For score >= 8, call generate_letter() for both NL and EN.
    Import from hestia.enrichment.letters.
    Only call if letter_nl/letter_en is still NULL in the DB."""

def run_analysis() -> None:
    """Main entry point. Steps:
    1. check_daily_budget() — if over, log and return
    2. drain_pending(limit=MAX_BATCH_SIZE)
    3. If empty, log and return
    4. Group items by profile_id (in case multiple profiles exist)
    5. For each profile group:
       a. Fetch detail pages for each item, store text via update_page_text()
       b. Build Claude prompt with fetched texts
       c. Call anthropic.Anthropic().messages.create() with retry (3 attempts, exp backoff)
       d. Parse response, store verdicts
       e. Send enriched Telegram messages
       f. Auto-generate letters for top-rated
       g. Mark items as done (or failed)
       h. Log token usage
    """

if __name__ == '__main__':
    import asyncio
    asyncio.run(run_analysis_async())
    # run_analysis_async is an async wrapper around run_analysis
    # that handles the event loop for Telegram sends
```

Important details:
- The Claude API key comes from environment variable `ANTHROPIC_API_KEY`. Use `anthropic.Anthropic()` which reads it automatically.
- Retry with `try/except anthropic.APIError`, sleep `2 ** attempt` seconds.
- After calling Claude, immediately call `log_usage()` with the token counts from `response.usage.input_tokens` and `response.usage.output_tokens`.
- For Telegram sends, use `meta.BOT.send_message()` with `parse_mode="MarkdownV2"`. Use `telegram.InlineKeyboardButton` and `telegram.InlineKeyboardMarkup` for buttons.
- Group low-scored listings (< 5) into one summary message to avoid spam.
- If a listing's page_text is None and screenshot_b64 is also None, mark as failed and skip.

### 3. Update `hestia/requirements.txt`

Add to the end:
```
anthropic >= 0.40.0
```

Do NOT add `playwright` yet — it's optional and agent 4 will handle that as part of Docker setup.

## Rules

- No bare `except`. Catch `anthropic.APIError`, `json.JSONDecodeError`, `requests.RequestException`, etc.
- `_parse_claude_response` must NEVER raise. This is the most critical function — it handles malformed input gracefully.
- Playwright import must be wrapped in try/except at module level.
- All logging at WARNING level for operational events, ERROR for failures.
- Type hints throughout.
- The analyzer runs as `__main__` — it's a standalone script invoked by cron.

## How to Verify

1. `_parse_claude_response` handles: valid JSON array, markdown-fenced JSON, truncated JSON, empty string, random text.
2. `_build_analysis_prompt` produces the exact schema from context.md.
3. `_send_enriched_message` produces valid MarkdownV2 (escape special chars via `meta.escape_markdownv2`).
4. `run_analysis` has proper error handling at every step — a failure in one listing doesn't crash the batch.
