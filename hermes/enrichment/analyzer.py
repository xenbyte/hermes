import json
import logging
import time
import uuid
import asyncio
from collections import defaultdict

import anthropic
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from hermes_utils.db import get_connection, _write, fetch_one
import hermes_utils.meta as meta

from hermes_utils.logging_config import setup_logging
from enrichment.profile import get_profile_by_id, build_system_prompt
from enrichment.queue import drain_pending, mark_done, mark_failed, update_page_text
from enrichment.fetcher import fetch_detail_page
from enrichment.costs import log_usage, check_daily_budget

logger = logging.getLogger(__name__)

ANALYSIS_MODEL = "claude-haiku-4-5-20251001"
MAX_BATCH_SIZE = 20
MAX_RETRIES = 3

_MARKDOWNV2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def _esc(text) -> str:
    """Escape all MarkdownV2 special characters."""
    text = str(text)
    text = text.replace("\\", "\\\\")
    for ch in _MARKDOWNV2_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_analysis_prompt(listings: list[dict]) -> str:
    """Build the Claude user prompt with the exact schema from context.md."""
    n = len(listings)
    items = json.dumps(listings, ensure_ascii=False)
    return (
        f"Analyze these {n} Dutch rental listings against the user profile.\n"
        f"Return ONLY a JSON array of {n} objects. No markdown, no explanation.\n\n"
        "Per-listing schema:\n"
        "{\n"
        '  "index": int,\n'
        '  "score": int (1-10),\n'
        '  "compatible": bool,\n'
        '  "confidence": "high|medium|low",\n'
        '  "rejection_reason": string or null,\n'
        '  "listing": {\n'
        '    "address": string, "city": string, "rent_per_month": int|null,\n'
        '    "size_m2": int|null, "rooms": int|null,\n'
        '    "furnished_status": "kaal|gestoffeerd|gemeubileerd|unknown",\n'
        '    "available_from": string|null, "energy_label": string|null,\n'
        '    "contact_email": string|null, "application_url": string|null\n'
        "  },\n"
        '  "income_check": { "required_income": int, "user_income": int, "passes": bool },\n'
        '  "trade_offs": [string],\n'
        '  "recommendation": string,\n'
        '  "expat_flags": [string]\n'
        "}\n\n"
        "Scoring: 9-10 perfect, 7-8 good, 5-6 acceptable, 3-4 poor, 1-2 incompatible.\n\n"
        f"Listings:\n{items}"
    )


# ---------------------------------------------------------------------------
# Response parsing — must NEVER raise
# ---------------------------------------------------------------------------

def _parse_claude_response(response_text: str) -> list[dict]:
    """Parse Claude's JSON response, handling fences, truncation, and garbage."""
    if not response_text or not response_text.strip():
        logger.error("Empty Claude response")
        return []

    text = response_text.strip()

    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            logger.warning("Claude returned a single object, wrapping in list")
            return [result]
        return []
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            result = json.loads(text[start : end + 1])
            if isinstance(result, list):
                logger.warning("Extracted JSON array from surrounding text")
                return result
        except json.JSONDecodeError:
            pass

    if start != -1:
        partial = text[start:]
        for suffix in ["]", "}]", '"}]', "null}]", 'null"}]']:
            try:
                result = json.loads(partial + suffix)
                if isinstance(result, list):
                    logger.warning(
                        "Recovered truncated JSON with suffix '%s' (%d items)",
                        suffix,
                        len(result),
                    )
                    return result
            except json.JSONDecodeError:
                continue

    logger.error("Failed to parse Claude response: %.200s", text)
    return []


# ---------------------------------------------------------------------------
# Result storage
# ---------------------------------------------------------------------------

def _store_verdict(queue_item: dict, verdict: dict, model: str) -> None:
    """INSERT or UPDATE enrichment_results from a Claude verdict."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO hermes.enrichment_results "
                "(id, profile_id, url, score, compatible, confidence, rejection_reason, "
                "listing_json, trade_offs, recommendation, income_check, expat_flags, model_used) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s) "
                "ON CONFLICT (id, profile_id) DO UPDATE SET "
                "score = EXCLUDED.score, compatible = EXCLUDED.compatible, "
                "confidence = EXCLUDED.confidence, rejection_reason = EXCLUDED.rejection_reason, "
                "listing_json = EXCLUDED.listing_json, trade_offs = EXCLUDED.trade_offs, "
                "recommendation = EXCLUDED.recommendation, income_check = EXCLUDED.income_check, "
                "expat_flags = EXCLUDED.expat_flags, model_used = EXCLUDED.model_used, "
                "analyzed_at = now()",
                [
                    queue_item["id"],
                    queue_item["profile_id"],
                    queue_item["url"],
                    verdict.get("score"),
                    verdict.get("compatible"),
                    verdict.get("confidence"),
                    verdict.get("rejection_reason"),
                    json.dumps(verdict.get("listing", {})),
                    verdict.get("trade_offs", []),
                    verdict.get("recommendation"),
                    json.dumps(verdict.get("income_check", {})),
                    verdict.get("expat_flags", []),
                    model,
                ],
            )
            conn.commit()
    except Exception as e:
        logger.error(
            "Failed to store verdict for queue_id=%s profile_id=%s: %r",
            queue_item["id"],
            queue_item["profile_id"],
            e,
        )
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Telegram messaging
# ---------------------------------------------------------------------------

async def _send_enriched_message(
    telegram_id: str, verdict: dict, url: str, result_id: str
) -> None:
    """Format and send a detailed enriched message for score >= 5."""
    listing = verdict.get("listing", {})
    score = verdict.get("score", 0)

    address = listing.get("address") or "Unknown"
    city = listing.get("city") or ""
    rent = listing.get("rent_per_month")
    sqm = listing.get("size_m2")
    rooms = listing.get("rooms")
    furnished = listing.get("furnished_status")
    available_from = listing.get("available_from")
    energy = listing.get("energy_label")

    lines: list[str] = []
    lines.append(f"\\-\\-\\- Score: {score}/10 \\-\\-\\-")
    lines.append(f"\U0001F3E0 *{_esc(address)}, {_esc(city)}*")

    price_parts: list[str] = []
    if rent:
        price_parts.append(f"\u20AC{rent:,}/month")
    if sqm and sqm > 0:
        price_parts.append(f"{sqm}m\u00B2")
    if rooms:
        price_parts.append(f"{rooms} rooms")
    if price_parts:
        lines.append(f"\U0001F4B6 {_esc(' \u00B7 '.join(price_parts))}")

    detail_parts: list[str] = []
    if furnished and furnished != "unknown":
        detail_parts.append(furnished.capitalize())
    if available_from:
        detail_parts.append(f"Available {available_from}")
    if detail_parts:
        lines.append(f"\U0001F6CB {_esc(' \u00B7 '.join(detail_parts))}")

    if energy:
        lines.append(f"\u26A1 Energy: {_esc(energy)}")

    recommendation = verdict.get("recommendation", "")
    if recommendation:
        lines.append("")
        lines.append(f"_{_esc(recommendation)}_")

    trade_offs = verdict.get("trade_offs", [])
    if trade_offs:
        lines.append("")
        lines.append("Trade\\-offs:")
        for item in trade_offs:
            lines.append(_esc(item))

    income_check = verdict.get("income_check", {})
    if income_check:
        required = income_check.get("required_income")
        user_inc = income_check.get("user_income")
        passes = income_check.get("passes")
        if required and user_inc:
            icon = "\u2705" if passes else "\u274C"
            lines.append(
                f"\n{icon} Income check: {_esc(f'\u20AC{required:,} required, you earn \u20AC{user_inc:,}')}"
            )

    expat_flags = verdict.get("expat_flags", [])
    if expat_flags:
        lines.append("")
        for flag in expat_flags:
            lines.append(f"\u26A0\uFE0F {_esc(flag)}")

    text = "\n".join(lines)

    callback_id = result_id[:48]
    application_url = listing.get("application_url") or url

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001F517 View Listing", url=url),
                InlineKeyboardButton(
                    "\U0001F4DD Letter NL",
                    callback_data=f"letter_nl:{callback_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001F4DD Letter EN",
                    callback_data=f"letter_en:{callback_id}",
                ),
                InlineKeyboardButton("\U0001F4E8 Apply", url=application_url),
            ],
        ]
    )

    try:
        await meta.BOT.send_message(
            chat_id=telegram_id,
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error("Failed to send enriched message to %s: %r", telegram_id, e)


async def _send_low_score_summary(
    telegram_id: str, low_scored: list[dict]
) -> None:
    """Send a single compact summary for all listings scored below 5."""
    if not low_scored:
        return

    lines: list[str] = []
    lines.append(f"*{_esc(f'{len(low_scored)} low-scored listing(s) skipped')}*")
    lines.append("")

    for entry in low_scored:
        item = entry["item"]
        verdict = entry["verdict"]
        score = verdict.get("score", 0)
        address = item.get("address", "Unknown")
        city = item.get("city", "")
        reason = verdict.get("rejection_reason") or verdict.get("recommendation", "")
        lines.append(
            f"\u2022 {_esc(f'{address}, {city}')} — {score}/10"
        )
        if reason:
            lines.append(f"  _{_esc(reason)}_")

    text = "\n".join(lines)

    try:
        await meta.BOT.send_message(
            chat_id=telegram_id,
            text=text,
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error("Failed to send low-score summary to %s: %r", telegram_id, e)


# ---------------------------------------------------------------------------
# Auto letter generation
# ---------------------------------------------------------------------------

async def _auto_generate_letters(
    profile: dict, verdict: dict, result_id: str, profile_id: int
) -> None:
    """For score >= 8, generate NL + EN letters if not already cached."""
    try:
        from enrichment.letters import generate_letter
    except ImportError:
        logger.warning("letters module not available, skipping auto-generation")
        return

    row = fetch_one(
        "SELECT letter_nl, letter_en FROM hermes.enrichment_results "
        "WHERE id = %s AND profile_id = %s",
        [result_id, profile_id],
    )

    for lang, col in [("nl", "letter_nl"), ("en", "letter_en")]:
        if row.get(col):
            continue
        try:
            letter = generate_letter(profile, verdict, lang)
            if letter:
                _write(
                    f"UPDATE hermes.enrichment_results SET {col} = %s "
                    "WHERE id = %s AND profile_id = %s",
                    [letter, result_id, profile_id],
                )
        except Exception as e:
            logger.error("Failed to generate %s letter for result %s: %r", lang, result_id, e)


# ---------------------------------------------------------------------------
# Main analysis loop
# ---------------------------------------------------------------------------

async def _run_analysis_async() -> None:
    """Core async analysis logic."""
    if not check_daily_budget():
        logger.warning("Daily LLM budget exceeded — skipping analysis run")
        return

    items = drain_pending(limit=MAX_BATCH_SIZE)
    if not items:
        logger.info("No pending items in enrichment queue")
        return

    logger.info("Processing %d enrichment queue items", len(items))

    groups: dict[int, list[dict]] = defaultdict(list)
    for item in items:
        groups[item["profile_id"]].append(item)

    client = anthropic.Anthropic()

    for profile_id, group_items in groups.items():
        logger.debug("Processing profile_id=%s with %d items", profile_id, len(group_items))
        profile = get_profile_by_id(profile_id)
        if not profile:
            logger.error("Profile %s not found, failing %d items", profile_id, len(group_items))
            for item in group_items:
                mark_failed(item["id"], item["profile_id"], "Profile not found")
            continue

        listings_for_claude: list[dict] = []
        fetchable_items: list[dict] = []

        for item in group_items:
            try:
                result = fetch_detail_page(item["url"], item.get("agency", ""))
                if result.text and len(result.text) >= 50:
                    update_page_text(
                        item["id"], item["profile_id"], result.text, result.method
                    )
                    listings_for_claude.append(
                        {
                            "index": len(fetchable_items),
                            "url": item["url"],
                            "text": result.text[:15_000],
                        }
                    )
                    fetchable_items.append(item)
                else:
                    mark_failed(
                        item["id"],
                        item["profile_id"],
                        "No usable text content fetched",
                    )
            except Exception as e:
                logger.error("Fetch failed for %s: %r", item["url"], e)
                mark_failed(item["id"], item["profile_id"], f"Fetch error: {e!r}")

        if not listings_for_claude:
            continue

        system_prompt = build_system_prompt(profile)
        user_prompt = _build_analysis_prompt(listings_for_claude)
        batch_id = str(uuid.uuid4())

        response = None
        for attempt in range(MAX_RETRIES):
            try:
                response = client.messages.create(
                    model=ANALYSIS_MODEL,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                break
            except anthropic.APIError as e:
                logger.error(
                    "Claude API error (attempt %d/%d): %r",
                    attempt + 1,
                    MAX_RETRIES,
                    e,
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2**attempt)

        if not response:
            for item in fetchable_items:
                mark_failed(
                    item["id"], item["profile_id"], "Claude API failed after retries"
                )
            continue

        log_usage(
            batch_id,
            ANALYSIS_MODEL,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        response_text = response.content[0].text if response.content else ""
        verdicts = _parse_claude_response(response_text)

        verdict_map: dict[int, dict] = {}
        for v in verdicts:
            idx = v.get("index")
            if idx is not None and 0 <= idx < len(fetchable_items):
                verdict_map[idx] = v

        low_scored: list[dict] = []

        for i, item in enumerate(fetchable_items):
            verdict = verdict_map.get(i)
            logger.debug("Verdict for item %s: score=%s compatible=%s", item["id"][:12], verdict.get("score") if verdict else None, verdict.get("compatible") if verdict else None)
            if not verdict:
                mark_failed(
                    item["id"], item["profile_id"], "No verdict returned by Claude"
                )
                continue

            try:
                _store_verdict(item, verdict, ANALYSIS_MODEL)
            except Exception as e:
                logger.error("Failed to store verdict for %s: %r", item["id"], e)
                mark_failed(item["id"], item["profile_id"], f"Store error: {e!r}")
                continue

            score = verdict.get("score", 0)
            if score >= 5:
                await _send_enriched_message(
                    profile["telegram_id"], verdict, item["url"], item["id"]
                )
                if score >= 8:
                    await _auto_generate_letters(
                        profile, verdict, item["id"], profile["id"]
                    )
            else:
                low_scored.append({"item": item, "verdict": verdict})

            mark_done(item["id"], item["profile_id"])

        if low_scored:
            await _send_low_score_summary(profile["telegram_id"], low_scored)

    logger.info("Analysis run complete")


def run_analysis() -> None:
    """Sync entry point for the scheduled cron job."""
    asyncio.run(_run_analysis_async())


if __name__ == "__main__":
    setup_logging()
    run_analysis()
