"""On-demand listing analysis triggered by the Telegram "Analyse" button."""

import hashlib
import json
import logging
import uuid

import anthropic

from enrichment.commute import get_commute_times, format_commute_times
from enrichment.costs import check_daily_budget, log_usage
from enrichment.fetcher import fetch_detail_page
import hermes_utils.db as db

logger = logging.getLogger(__name__)

ANALYSIS_MODEL = "claude-haiku-4-5-20251001"

_MARKDOWNV2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def _esc(text: object) -> str:
    text = str(text)
    text = text.replace("\\", "\\\\")
    for ch in _MARKDOWNV2_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_prompt(page_text: str, work_address: str | None) -> str:
    work_note = (
        f"\nWork address (for commute estimation): {work_address}"
        if work_address
        else "\nWork address: not provided — skip commute or estimate from city center."
    )
    return (
        f"Analyze this Dutch rental listing.{work_note}\n\n"
        "Return ONLY a JSON object with this exact schema (no markdown, no extra text):\n"
        "{\n"
        '  "listing": {\n'
        '    "rooms": int|null,\n'
        '    "floor": "string (e.g. \\"3rd floor\\") or null",\n'
        '    "energy_label": "A/B/C/D/E/F/G or null",\n'
        '    "furnished": "kaal|gestoffeerd|gemeubileerd|unknown",\n'
        '    "available_from": "string or null",\n'
        '    "deposit": int|null,\n'
        '    "pets_allowed": true|false|null,\n'
        '    "description_snippet": "first ~150 chars of the main listing description"\n'
        '  },\n'
        '  "score": int,\n'
        '  "compatible": bool,\n'
        '  "income_check": {"required_income": int|null, "user_income": int|null, "passes": bool|null},\n'
        '  "neighborhood": "2-3 sentences: area quality, safety, vibe, walkability",\n'
        '  "supermarkets": "nearest supermarkets with estimated distance",\n'
        '  "commute": "estimated commute from listing to work address by bike/transit/car — or null if no work address",\n'
        '  "pros": ["string", "string", "string"],\n'
        '  "cons": ["string", "string", "string"],\n'
        '  "recommendation": "1-2 sentences: verdict and what to check on viewings"\n'
        "}\n\n"
        "Scoring: 9-10 perfect match, 7-8 good, 5-6 acceptable, 3-4 poor, 1-2 incompatible.\n"
        "Use your knowledge of Dutch cities for neighborhood and amenities.\n\n"
        f"Listing page content:\n{page_text[:8000]}"
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _score_emoji(score: int) -> str:
    if score >= 8:
        return "🟢"
    if score >= 6:
        return "🟡"
    if score >= 4:
        return "🟠"
    return "🔴"


def _format_reply(home: dict, data: dict, commute_override: str | None) -> str:
    listing = data.get("listing", {})
    score = data.get("score", 0)

    lines: list[str] = []

    address = home.get("address", "")
    city = home.get("city", "")
    header = f"{_esc(address)}, {_esc(city)}" if address or city else _esc(home.get("url", ""))
    lines.append(f"📊 *{header}*")
    lines.append(f"{_score_emoji(score)} Score: {score}/10")
    lines.append("")

    # Listing details block
    detail_parts: list[str] = []
    if listing.get("rooms"):
        detail_parts.append(f"🛏 {listing['rooms']} rooms")
    if listing.get("floor"):
        detail_parts.append(f"🏢 {_esc(listing['floor'])}")
    if listing.get("energy_label"):
        detail_parts.append(f"⚡ Energy: {_esc(listing['energy_label'])}")
    furnished = listing.get("furnished", "unknown")
    if furnished and furnished != "unknown":
        detail_parts.append(f"🛋 {_esc(furnished.capitalize())}")
    if listing.get("available_from"):
        detail_parts.append(f"📅 {_esc(listing['available_from'])}")
    if listing.get("deposit"):
        deposit = listing["deposit"]
        detail_parts.append(f"💰 Deposit: €{deposit:,}" if isinstance(deposit, int) else f"💰 Deposit: {_esc(str(deposit))}")
    if listing.get("pets_allowed") is not None:
        detail_parts.append("🐾 Pets: ✅" if listing["pets_allowed"] else "🐾 Pets: ❌")

    if detail_parts:
        lines.append("*Listing details*")
        lines.append("  ·  ".join(detail_parts))
        if listing.get("description_snippet"):
            lines.append(f"_{_esc(listing['description_snippet'][:180])}_")
        lines.append("")

    # Neighborhood
    if data.get("neighborhood"):
        lines.append("📍 *Neighborhood*")
        lines.append(_esc(data["neighborhood"]))
        lines.append("")

    # Supermarkets
    if data.get("supermarkets"):
        lines.append("🛒 *Nearby*")
        lines.append(_esc(data["supermarkets"]))
        lines.append("")

    # Commute — prefer Google Maps override, fall back to Claude's estimate
    commute_text = commute_override or data.get("commute")
    if commute_text:
        lines.append("🚗 *Commute to work*")
        lines.append(_esc(commute_text))
        lines.append("")

    # Income check
    ic = data.get("income_check", {})
    if ic and ic.get("required_income") and ic.get("user_income"):
        passes = ic.get("passes")
        icon = "✅" if passes else "❌"
        req = ic["required_income"]
        earned = ic["user_income"]
        try:
            lines.append(f"{icon} *Income check*: €{int(req):,} required, you earn €{int(earned):,}")
        except (ValueError, TypeError):
            lines.append(f"{icon} *Income check*: €{_esc(str(req))} required, you earn €{_esc(str(earned))}")
        lines.append("")

    # Pros / Cons
    pros = data.get("pros") or []
    cons = data.get("cons") or []
    if pros or cons:
        lines.append("*⚖️ Pros / Cons*")
        for p in pros:
            lines.append(f"\\+ {_esc(p)}")
        for c in cons:
            lines.append(f"\\- {_esc(c)}")
        lines.append("")

    # Recommendation
    if data.get("recommendation"):
        lines.append(f"💡 _{_esc(data['recommendation'])}_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def get_cached_reply(url_hash: str, profile_id: int) -> str | None:
    row = db.fetch_one(
        "SELECT reply_text FROM hermes.listing_analysis WHERE url_hash = %s AND profile_id = %s",
        [url_hash, profile_id],
    )
    return row.get("reply_text") if row else None


def _save_analysis(
    url_hash: str,
    profile_id: int,
    url: str,
    listing_json: dict,
    verdict_json: dict,
    reply_text: str,
) -> None:
    db._write(
        "INSERT INTO hermes.listing_analysis "
        "(url_hash, profile_id, url, listing_json, verdict_json, reply_text) "
        "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s) "
        "ON CONFLICT (url_hash, profile_id) DO UPDATE SET "
        "listing_json = EXCLUDED.listing_json, "
        "verdict_json = EXCLUDED.verdict_json, "
        "reply_text   = EXCLUDED.reply_text, "
        "created_at   = NOW()",
        [url_hash, profile_id, url, json.dumps(listing_json), json.dumps(verdict_json), reply_text],
    )


# ---------------------------------------------------------------------------
# Main entry point (sync — call via asyncio.to_thread from the bot)
# ---------------------------------------------------------------------------

def _run_core(home: dict, profile: dict, telegram_id: str) -> str:
    """
    Shared analysis core: fetch page → Claude → format → cache.
    home must have at least 'url'. agency, address, city are optional.
    """
    from enrichment.profile import build_system_prompt

    profile_id = profile["id"]
    url = home["url"]
    url_hash = home.get("url_hash") or hashlib.sha256(url.encode()).hexdigest()[:32]

    # Cache hit — never counts against daily limit
    cached = get_cached_reply(url_hash, profile_id)
    if cached:
        logger.info("on_demand: cache hit url_hash=%s profile_id=%s", url_hash, profile_id)
        return cached

    # Access gate
    limit = db.get_analysis_limit(int(telegram_id))
    if limit == 0:
        state = db.get_ai_access_state(int(telegram_id))
        if state == "pending":
            return (
                "⏳ Your AI access request is still pending\\. "
                "You'll get a message here once an admin reviews it\\."
            )
        return (
            "🔒 AI analysis is not enabled for your account\\.\n"
            "Send /request\\_ai to ask an admin for access\\."
        )
    if limit != -1:
        used = db.get_daily_analysis_count(profile_id)
        if used >= limit:
            return (
                f"⚠️ You've used all {limit} AI analyses for today\\.\n"
                "Resets at midnight UTC\\."
            )

    if not check_daily_budget():
        return "⚠️ Daily AI budget exceeded\\. Try again tomorrow\\."

    # Check if analysis is enabled for this agency
    agency = home.get("agency", "")
    agency_config = db.get_agency_detail_config(agency)
    if not agency_config["ai_analysis_enabled"]:
        return (
            f"❌ AI analysis is not supported for *{_esc(agency)}* listings yet\\.\n"
            "Only Pararius is currently supported\\."
        )

    # Fetch detail page
    fetch_result = fetch_detail_page(url, agency, agency_config["detail_fetch_method"])
    if not fetch_result.text or len(fetch_result.text) < 50:
        return (
            "❌ Couldn't fetch the listing page \\(the site may block automated access\\)\\. "
            "Try opening the link directly\\."
        )

    # Optional Google Maps commute (only when we have a real address)
    commute_override: str | None = None
    work_address = profile.get("work_address")
    if work_address and home.get("address"):
        home_address = f"{home['address']}, {home.get('city', '')}, Netherlands"
        times = get_commute_times(home_address, work_address)
        if times:
            commute_override = format_commute_times(times)

    # Claude call
    system_prompt = build_system_prompt(profile)
    user_prompt = _build_prompt(fetch_result.text, work_address)

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=ANALYSIS_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.APIError as e:
        logger.error("on_demand: Claude API error: %r", e)
        return "❌ AI analysis failed\\. Please try again\\."

    log_usage(str(uuid.uuid4()), ANALYSIS_MODEL, response.usage.input_tokens, response.usage.output_tokens)

    response_text = response.content[0].text if response.content else ""

    try:
        text = response_text.strip()
        if text.startswith("```"):
            text = text[text.find("\n") + 1:]
            if text.endswith("```"):
                text = text[:-3]
        data = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("on_demand: failed to parse Claude response: %r\n%.300s", e, response_text)
        return f"📊 *Analysis*\n\n{_esc(response_text[:2000])}"

    reply_text = _format_reply(dict(home), data, commute_override)
    _save_analysis(url_hash, profile_id, url, data.get("listing", {}), data, reply_text)

    logger.info(
        "on_demand: done url_hash=%s profile_id=%s score=%s method=%s",
        url_hash, profile_id, data.get("score"), fetch_result.method,
    )
    return reply_text


def run_on_demand_analysis(url_hash: str, telegram_id: str) -> str:
    """Entry point for the Analyse button callback (url_hash already known)."""
    from enrichment.profile import get_profile_for_telegram_id

    profile = get_profile_for_telegram_id(telegram_id)
    if not profile:
        return "❌ No profile found\\. Use /profile setup to create one first\\."

    home = db.fetch_one("SELECT * FROM hermes.homes WHERE url_hash = %s", [url_hash])
    if not home:
        return "❌ Listing not found in database\\."

    return _run_core(dict(home), profile, telegram_id)


def run_on_demand_analysis_by_url(url: str, telegram_id: str) -> str:
    """Entry point for the /analyse <url> command."""
    from enrichment.profile import get_profile_for_telegram_id
    from urllib.parse import urlparse

    profile = get_profile_for_telegram_id(telegram_id)
    if not profile:
        return "❌ No profile found\\. Use /profile setup to create one first\\."

    # Prefer DB record so we have structured address/city/price data
    home = db.fetch_one("SELECT * FROM hermes.homes WHERE url = %s", [url])
    if not home:
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:32]
        home = db.fetch_one("SELECT * FROM hermes.homes WHERE url_hash = %s", [url_hash])

    if not home:
        # Not scraped yet — build a minimal record from the URL
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        agency = domain.split(".")[0]
        home = {
            "url": url,
            "url_hash": hashlib.sha256(url.encode()).hexdigest()[:32],
            "agency": agency,
            "address": "",
            "city": "",
            "price": 0,
            "sqm": 0,
        }

    return _run_core(dict(home), profile, telegram_id)
