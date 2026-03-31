# Agent 3: Telegram Integration + Scraper Wiring

Read `@plans/context.md` first for full project context.

## Prerequisites

Agents 1 and 2 must be complete. Verify these files exist before starting:
- `hestia/enrichment/__init__.py`
- `hestia/enrichment/profile.py`
- `hestia/enrichment/queue.py`
- `hestia/enrichment/costs.py`
- `hestia/enrichment/fetcher.py`
- `hestia/enrichment/analyzer.py`

Read all of them to understand the interfaces.

## Your Job

1. `hestia/enrichment/letters.py` — motivation letter generator with caching
2. `hestia/enrichment/prefilter.py` — pre-filter + enqueue function
3. Modify `hestia/scraper.py` — wire in `enqueue_for_enrichment()` after broadcast
4. Modify `hestia/bot.py` — add letter callbacks, `/profile` command, `/cost` command

## Files to Read First

- `hestia/bot.py` — understand ALL existing handlers, especially `callback_query_handler()` (line 419+). Your letter callbacks must coexist with the existing `hfa.*` agency filter callbacks.
- `hestia/scraper.py` — understand `scrape_site()` (line 250+) and where to add the enqueue call.
- `hestia/hestia_utils/meta.py` — `BOT`, `escape_markdownv2()`, emoji constants
- `hestia/hestia_utils/db.py` — DB patterns for reads/writes
- `hestia/hestia_utils/strings.py` — understand how localized strings work (you don't need to add to this, just be aware)
- `hestia/hestia_utils/secrets.py` (template at `misc/secrets.py.template`) — you'll need `OWN_CHAT_ID` for error notifications
- `hestia/enrichment/profile.py` — for `get_profile_for_telegram_id()`, `get_profiles_with_enrichment()`, `build_system_prompt()`, `upsert_profile()`
- `hestia/enrichment/queue.py` — for `enqueue()`
- `hestia/enrichment/costs.py` — for `get_daily_spend()`, `get_monthly_summary()`
- `hestia/enrichment/analyzer.py` — for understanding the verdict/result schema stored in DB

## Files to Create

### 1. `hestia/enrichment/prefilter.py`

```python
import logging
from hestia_utils.parser import Home
from hestia.enrichment.profile import get_profiles_with_enrichment
from hestia.enrichment.queue import enqueue

def should_enqueue(home: Home, profile: dict) -> bool:
    """Return True if:
    - home.price > 0 (valid price)
    - home.price <= profile['max_rent']
    - home.city.lower() in [c.lower() for c in profile['target_cities']]
    Log rejections at DEBUG level."""

def enqueue_for_enrichment(new_homes: list[Home]) -> None:
    """Called from scraper.py after broadcast().
    1. Load all profiles via get_profiles_with_enrichment()
    2. If no profiles exist, return immediately (no-op)
    3. For each home, for each profile: if should_enqueue(), call enqueue(home, profile_id)
    4. Log summary: 'Enrichment: enqueued {n} of {total} new homes for {p} profiles'
    Catch all exceptions — this must NEVER crash the scraper."""
```

### 2. `hestia/enrichment/letters.py`

```python
import logging
import anthropic
from hestia_utils.db import get_connection, fetch_one, _write
from hestia.enrichment.profile import build_system_prompt
from hestia.enrichment.costs import log_usage

LETTER_MODEL = "claude-sonnet-4-20250514"

def _get_cached_letter(result_id: str, language: str) -> str | None:
    """Check enrichment_results.letter_nl or letter_en.
    Return the cached text if non-null."""

def _cache_letter(result_id: str, language: str, letter: str) -> None:
    """UPDATE enrichment_results SET letter_{language} = letter WHERE id = result_id."""

def generate_letter(profile: dict, verdict: dict, language: str) -> str:
    """Generate a motivation letter.
    1. Check cache first via _get_cached_letter(). If cached, return it.
    2. Build system prompt from profile.
    3. Build user prompt referencing the specific listing from verdict.
    4. Call Claude Sonnet with retry (3 attempts, exp backoff).
    5. Cache the result.
    6. Log token usage.
    7. Return the letter text.
    language must be 'nl' or 'en'. 'nl' = Dutch, 'en' = English.
    On API failure after retries: return a clear error string, never raise."""
```

## Files to Modify

### 3. `hestia/scraper.py`

Add ONE import and ONE function call. Minimal change.

At the top, add import:
```python
from hestia.enrichment.prefilter import enqueue_for_enrichment
```

In `scrape_site()`, after the `await broadcast(new_homes)` call (line ~282), add:
```python
        try:
            enqueue_for_enrichment(new_homes)
        except Exception as e:
            logging.error(f"Enrichment enqueue failed: {repr(e)}")
```

That's it. The try/except ensures enrichment failures never break the existing scraper flow.

### 4. `hestia/bot.py`

Three additions:

#### A. Letter callback in `callback_query_handler()`

The existing handler (line 419) splits on `.` for `hfa` callbacks. Letter callbacks use `:` as separator. Modify the function to handle both:

```python
async def callback_query_handler(update: telegram.Update, _) -> None:
    query = update.callback_query
    if not query or not query.data or not query.message: return

    # Existing agency filter callbacks (dot-separated)
    if query.data.startswith("hfa."):
        cbid, action, agency = query.data.split(".")
        # ... existing hfa logic unchanged ...

    # NEW: Letter generation callbacks (colon-separated)
    elif query.data.startswith("letter_"):
        parts = query.data.split(":", 1)
        if len(parts) != 2:
            return
        action, result_id = parts  # action = "letter_nl" or "letter_en"
        language = "nl" if action == "letter_nl" else "en"

        await query.answer("Generating letter, please wait...")

        # Load profile for this user
        from hestia.enrichment.profile import get_profile_for_telegram_id
        from hestia.enrichment.letters import generate_letter
        from hestia_utils.db import fetch_one

        profile = get_profile_for_telegram_id(str(query.message.chat.id))
        if not profile:
            await query.message.reply_text("No profile found. Use /profile set to create one.")
            return

        verdict = fetch_one("SELECT * FROM hestia.enrichment_results WHERE id = %s", [result_id])
        if not verdict:
            await query.message.reply_text("Listing analysis not found.")
            return

        letter = generate_letter(profile, dict(verdict), language)
        # Send as a reply to the original enriched message
        await query.message.reply_text(letter)
```

#### B. `/profile` command

Add a new handler function:

```python
async def profile_cmd(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return

    from hestia.enrichment.profile import get_profile_for_telegram_id, upsert_profile

    text = update.message.text.strip()
    parts = text.split(maxsplit=2)

    # /profile — show current
    if len(parts) == 1:
        profile = get_profile_for_telegram_id(str(update.effective_chat.id))
        if not profile:
            await context.bot.send_message(update.effective_chat.id, "No profile set. Use /profile edit <field> <value> to set fields.\n\nRequired: full_name, max_rent, target_cities")
            return
        # Format profile as readable text
        msg = "Your profile:\n\n"
        for key in ['full_name', 'age', 'nationality', 'employer', 'contract_type',
                     'gross_monthly_income', 'work_address', 'max_rent', 'target_cities',
                     'furnishing_pref', 'occupants', 'pets', 'move_in_date']:
            val = profile.get(key)
            if val is not None:
                msg += f"{key}: {val}\n"
        await context.bot.send_message(update.effective_chat.id, msg)

    # /profile edit <field> <value>
    elif len(parts) >= 3 and parts[1] == 'edit':
        field_and_value = text.split(maxsplit=3)
        if len(field_and_value) < 4:
            await context.bot.send_message(update.effective_chat.id, "Usage: /profile edit <field> <value>")
            return
        field = field_and_value[2]
        value = field_and_value[3]

        allowed = {'full_name', 'age', 'nationality', 'languages', 'bsn_held', 'gemeente',
                   'employer', 'contract_type', 'gross_monthly_income', 'employment_duration',
                   'work_address', 'max_rent', 'target_cities', 'furnishing_pref', 'occupants',
                   'pets', 'owned_items', 'move_in_date', 'extra_notes'}
        if field not in allowed:
            await context.bot.send_message(update.effective_chat.id, f"Unknown field: {field}\nAllowed: {', '.join(sorted(allowed))}")
            return

        # Type coercion for specific fields
        if field in ('age', 'gross_monthly_income', 'max_rent'):
            try:
                value = int(value)
            except ValueError:
                await context.bot.send_message(update.effective_chat.id, f"{field} must be a number")
                return
        elif field == 'target_cities':
            value = [c.strip() for c in value.split(',')]
        elif field == 'languages':
            value = [l.strip() for l in value.split(',')]
        elif field == 'bsn_held':
            value = value.lower() in ('true', 'yes', '1')

        upsert_profile(str(update.effective_chat.id), {field: value})
        await context.bot.send_message(update.effective_chat.id, f"Updated {field}")

    else:
        await context.bot.send_message(update.effective_chat.id, "Usage:\n/profile — view\n/profile edit <field> <value>")
```

#### C. `/cost` command

```python
async def cost_cmd(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat: return

    from hestia.enrichment.costs import get_daily_spend, get_monthly_summary

    daily = get_daily_spend()
    monthly = get_monthly_summary()

    msg = f"LLM Costs\n\nToday: ${daily:.4f}\n"
    msg += f"This month: ${monthly.get('total_cost', 0):.4f}\n"
    msg += f"Total calls: {monthly.get('total_calls', 0)}\n"
    if monthly.get('by_model'):
        msg += "\nBy model:\n"
        for model, cost in monthly['by_model'].items():
            msg += f"  {model}: ${cost:.4f}\n"

    await context.bot.send_message(update.effective_chat.id, msg)
```

#### D. Register handlers

In the `if __name__ == '__main__'` block of `bot.py`, add before `application.run_polling()`:

```python
    application.add_handler(CommandHandler("profile", profile_cmd))
    application.add_handler(CommandHandler("cost", cost_cmd))
```

The callback handler registration already exists — the modified `callback_query_handler` handles both old and new callbacks.

## Rules

- The scraper.py change must be wrapped in try/except — enrichment must NEVER crash the scraper.
- Imports from `hestia.enrichment.*` in `bot.py` should be done inside the handler functions (lazy imports) to avoid import errors if enrichment modules aren't installed.
- Letter generation callbacks must call `query.answer()` before doing slow work (Claude API call), so Telegram doesn't show a loading spinner timeout.
- The `/profile` command does NOT require admin privileges — any subscriber can set their own profile.
- The `/cost` command could be admin-only (use the existing `privileged()` check) or open to any user with a profile. Your choice — lean toward open since each user only sees their own costs.

## How to Verify

1. `scraper.py` modification: one import + one try/except wrapped call. Nothing else changed.
2. `callback_query_handler` still handles `hfa.*` callbacks identically to before.
3. Letter callbacks: check cache, generate if needed, send as reply.
4. `/profile` without args shows profile, `/profile edit field value` updates it.
5. `/cost` shows daily and monthly spend.
