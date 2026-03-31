import json
import logging
import time
import uuid

import anthropic

from hestia_utils.db import fetch_one, _write
from enrichment.profile import build_system_prompt
from enrichment.costs import log_usage

logger = logging.getLogger(__name__)

LETTER_MODEL = "claude-sonnet-4-20250514"
MAX_RETRIES = 3


def _get_cached_letter(result_id: str, profile_id: int, language: str) -> str | None:
    """Return cached letter text from enrichment_results, or None."""
    col = f"letter_{language}"
    row = fetch_one(
        f"SELECT {col} FROM hestia.enrichment_results "
        "WHERE id = %s AND profile_id = %s",
        [result_id, profile_id],
    )
    return row.get(col) if row else None


def _cache_letter(result_id: str, profile_id: int, language: str, letter: str) -> None:
    """Persist generated letter into the enrichment_results cache column."""
    col = f"letter_{language}"
    _write(
        f"UPDATE hestia.enrichment_results SET {col} = %s "
        "WHERE id = %s AND profile_id = %s",
        [letter, result_id, profile_id],
    )


def generate_letter(profile: dict, verdict: dict, language: str) -> str:
    """Generate a motivation letter via Claude Sonnet.

    Checks the DB cache first. On API failure after retries, returns a
    human-readable error string (never raises).
    """
    result_id = verdict.get("id")
    profile_id = profile.get("id")

    if result_id and profile_id:
        cached = _get_cached_letter(result_id, profile_id, language)
        if cached:
            return cached

    system_prompt = build_system_prompt(profile)

    listing = verdict.get("listing_json") or verdict.get("listing") or {}
    if isinstance(listing, str):
        try:
            listing = json.loads(listing)
        except (json.JSONDecodeError, TypeError):
            listing = {}

    address = listing.get("address") or verdict.get("address", "Unknown")
    city = listing.get("city") or verdict.get("city", "")
    rent = listing.get("rent_per_month") or verdict.get("price", "")

    lang_name = "Dutch" if language == "nl" else "English"

    user_prompt = (
        f"Write a professional rental application motivation letter in {lang_name} "
        f"for the listing at {address}, {city} at EUR {rent}/month.\n"
        f"Ready to copy-paste. Address formally. Reference the specific property.\n"
        f"Listing details: {json.dumps(listing, ensure_ascii=False)}"
    )

    client = anthropic.Anthropic()
    batch_id = str(uuid.uuid4())
    response = None

    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=LETTER_MODEL,
                max_tokens=2048,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            break
        except anthropic.APIError as e:
            logger.error(
                "Claude API error (attempt %d/%d): %r",
                attempt + 1, MAX_RETRIES, e,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

    if not response:
        return "Failed to generate letter. Please try again later."

    log_usage(
        batch_id,
        LETTER_MODEL,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )

    letter = response.content[0].text if response.content else ""

    if not letter:
        return "Failed to generate letter content."

    if result_id and profile_id:
        _cache_letter(result_id, profile_id, language, letter)

    return letter
