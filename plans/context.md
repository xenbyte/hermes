# Hestia Enrichment Extension — Full Context

Read this file first. It describes the entire project. Your specific job is in a separate agent file.

## What Hestia Is

Hestia is a multi-subscriber Dutch rental listing bot. Two processes run as separate Docker containers sharing one PostgreSQL instance:

- **Scraper** (`hestia/scraper.py`): Cron every 5 minutes. Loops all enabled targets, fetches search results via HTTP, parses them into `Home` objects, deduplicates against `hestia.homes` (past 180 days by address+city), writes new homes to DB, then broadcasts to subscribers via Telegram.
- **Bot** (`hestia/bot.py`): Long-polling Telegram bot. Handles user commands (`/start`, `/stop`, `/filter`, `/help`, etc.) and inline keyboard callbacks.

### Key Existing Files

| File | Role |
|------|------|
| `hestia/scraper.py` | Scrape loop, `scrape_site()`, `broadcast()` |
| `hestia/bot.py` | Telegram command handlers, `callback_query_handler()` |
| `hestia/hestia_utils/parser.py` | `Home` class (plain class, not dataclass) + 25+ site parsers in `HomeResults` |
| `hestia/hestia_utils/db.py` | PostgreSQL access: `fetch_one()`, `fetch_all()`, `_write()`, `get_connection()` |
| `hestia/hestia_utils/meta.py` | Shared `BOT` instance, emoji constants, `escape_markdownv2()` |
| `hestia/hestia_utils/secrets.py` | Not committed. Contains `TOKEN`, `DB` dict, `OWN_CHAT_ID`, `APNS` |
| `hestia/requirements.txt` | Dependencies for bot + scraper |
| `docker/docker-compose.yml` | Services: hestia-bot, hestia-scraper, hestia-web, postgres |

### How `Home` Works

```python
class Home:
    def __init__(self, address='', city='', url='', agency='', price=-1, sqm=-1):
        self.address = address
        self.city = city      # normalized via property setter (strips province, maps variants)
        self.url = url
        self.agency = agency
        self.price = price
        self.sqm = sqm

    def __eq__(self, other):
        return self.address.lower() == other.address.lower() and self.city.lower() == other.city.lower()
```

### How `scrape_site()` Works

```python
async def scrape_site(target: dict) -> None:
    r = requests.get/post(target["queryurl"], ...)
    if r.status_code == 200:
        prev_homes = [Home(h["address"], h["city"]) for h in db.fetch_all("SELECT address, city FROM hestia.homes WHERE date_added > now() - interval '180 day'")]
        new_homes = [home for home in HomeResults(target["agency"], r) if home not in prev_homes]
        for home in new_homes:
            db.add_home(home.url, home.address, home.city, home.price, home.agency, datetime.now().isoformat(), home.sqm)
        await broadcast(new_homes)
        # ← NEW: enqueue_for_enrichment(new_homes) goes here
```

### How `broadcast()` Works

Loads all subscribers, loops each home x each subscriber, applies per-subscriber filters (price range, cities, agencies, sqm), sends MarkdownV2 Telegram message with house/price/link.

### How `callback_query_handler()` Works

Splits callback data on `.` — currently only handles `hfa.d.{agency}` and `hfa.e.{agency}` for agency filter toggles. New enrichment callbacks will use `:` as separator (e.g. `letter_nl:{result_id}`).

### How `db.py` Works

All DB access goes through `get_connection()` → `psycopg2.connect()` using `secrets.DB` dict. Pattern:
- Reads: `fetch_one(query, params)` / `fetch_all(query, params)` — returns dict/list via `RealDictCursor`
- Writes: `_write(query, params)` — executes + commits

Logging goes to `/data/hestia.log`.

---

## What We Are Building

An enrichment layer that:
1. Pre-filters new listings against user profiles (zero LLM cost)
2. Queues matching listings for batch Claude analysis
3. Runs a scheduled analyzer that scores listings 1-10 with trade-offs
4. Sends enriched Telegram messages with scores and inline keyboards
5. Generates motivation letters (auto for top-rated, on-demand for others)
6. Tracks LLM costs with a daily budget guard

### Architecture

```
scraper.py (every 5 min, existing)
  ├── scrape_site() → new_homes
  ├── broadcast(new_homes)           [EXISTING, untouched]
  └── enqueue_for_enrichment(new_homes)  [NEW, added after broadcast]
        └── for each profile: pre-filter price+city → insert enrichment_queue

analyzer.py (new cron, every 4 hours)
  ├── check daily budget → skip if over limit
  ├── drain enrichment_queue (status='pending')
  ├── fetch detail pages (HTTP → Playwright fallback)
  ├── batch into single Claude Haiku call
  ├── parse JSON verdicts → write enrichment_results
  ├── send enriched Telegram messages
  └── auto-generate letters for score >= 8

bot.py (long-polling, existing)
  ├── existing handlers [UNTOUCHED]
  └── NEW handlers:
      ├── callback: letter_nl:{id} → generate/cache Dutch letter
      ├── callback: letter_en:{id} → generate/cache English letter
      ├── /profile → view/set profile
      └── /cost → view LLM spend
```

---

## Database Schema (New Tables)

All in existing `hestia` schema. Migration SQL goes in `misc/sql/`.

### hestia.user_profiles

```sql
CREATE TABLE hestia.user_profiles (
    id                   SERIAL PRIMARY KEY,
    telegram_id          TEXT NOT NULL UNIQUE,
    full_name            TEXT NOT NULL,
    age                  INTEGER,
    nationality          TEXT,
    languages            TEXT[],
    bsn_held             BOOLEAN DEFAULT false,
    gemeente             TEXT,
    employer             TEXT,
    contract_type        TEXT,
    gross_monthly_income INTEGER,
    employment_duration  TEXT,
    work_address         TEXT,
    max_rent             INTEGER NOT NULL,
    target_cities        TEXT[] NOT NULL,
    furnishing_pref      TEXT DEFAULT 'either',
    occupants            TEXT DEFAULT 'single',
    pets                 TEXT,
    owned_items          TEXT,
    move_in_date         TEXT,
    extra_notes          TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now()
);
```

### hestia.enrichment_queue

```sql
CREATE TABLE hestia.enrichment_queue (
    id           TEXT PRIMARY KEY,       -- sha256(url)
    profile_id   INTEGER REFERENCES hestia.user_profiles(id),
    url          TEXT NOT NULL,
    address      TEXT,
    city         TEXT,
    price        INTEGER,
    agency       TEXT,
    sqm          INTEGER DEFAULT -1,
    enqueued_at  TIMESTAMPTZ DEFAULT now(),
    status       TEXT DEFAULT 'pending', -- pending | processing | done | failed
    retry_count  INTEGER DEFAULT 0,
    page_text    TEXT,
    fetch_method TEXT                    -- http | playwright_text | playwright_screenshot
);
```

### hestia.enrichment_results

```sql
CREATE TABLE hestia.enrichment_results (
    id               TEXT PRIMARY KEY,   -- same sha256(url) as queue
    profile_id       INTEGER REFERENCES hestia.user_profiles(id),
    url              TEXT NOT NULL,
    score            INTEGER,            -- 1-10
    compatible       BOOLEAN,
    confidence       TEXT,               -- high | medium | low
    rejection_reason TEXT,
    listing_json     JSONB,
    trade_offs       TEXT[],
    recommendation   TEXT,
    income_check     JSONB,
    expat_flags      TEXT[],
    letter_nl        TEXT,               -- cached, null until generated
    letter_en        TEXT,               -- cached, null until generated
    analyzed_at      TIMESTAMPTZ DEFAULT now(),
    model_used       TEXT
);
```

### hestia.llm_usage

```sql
CREATE TABLE hestia.llm_usage (
    id             SERIAL PRIMARY KEY,
    batch_id       TEXT,
    model          TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    estimated_cost NUMERIC(10,6),
    called_at      TIMESTAMPTZ DEFAULT now()
);
```

---

## Interface Contracts

Every agent MUST use these exact function signatures. Do not rename or change return types.

### profile.py

```python
def get_profiles_with_enrichment() -> list[dict]:
    """Return all user_profiles rows that have max_rent and target_cities set."""

def get_profile_by_id(profile_id: int) -> dict | None:
    """Return a single profile by its id."""

def get_profile_for_telegram_id(telegram_id: str) -> dict | None:
    """Return profile for a telegram user, or None."""

def build_system_prompt(profile: dict) -> str:
    """Render a profile dict into the Claude system prompt string."""
```

### prefilter.py

```python
def should_enqueue(home: Home, profile: dict) -> bool:
    """True if home.price <= profile['max_rent'] and home.city matches profile['target_cities']."""

def enqueue_for_enrichment(new_homes: list[Home]) -> None:
    """Load all profiles, pre-filter each home, enqueue matches. Called from scraper.py."""
```

### queue.py

```python
def enqueue(home: Home, profile_id: int) -> bool:
    """Insert into enrichment_queue. Returns False if duplicate (same sha256(url)+profile_id)."""

def drain_pending(limit: int = 50) -> list[dict]:
    """SELECT * FROM enrichment_queue WHERE status='pending' ORDER BY enqueued_at LIMIT {limit}. Sets status='processing'."""

def mark_done(queue_id: str) -> None:
def mark_failed(queue_id: str, reason: str) -> None:
def increment_retry(queue_id: str) -> None:
```

### fetcher.py

```python
@dataclass
class FetchResult:
    text: str | None
    screenshot_b64: str | None
    method: str   # "http" | "playwright_text" | "playwright_screenshot"

def fetch_detail_page(url: str, agency: str) -> FetchResult:
    """Tiered fetch: HTTP+BS4 first, Playwright fallback if text < 300 chars."""
```

### analyzer.py

```python
def run_analysis() -> None:
    """Entry point for the scheduled cron job. Drains queue, fetches, calls Claude, stores results, sends messages."""
```

### letters.py

```python
def generate_letter(profile: dict, verdict: dict, language: str) -> str:
    """Generate motivation letter via Claude Sonnet. language is 'nl' or 'en'. Checks cache first."""
```

### costs.py

```python
def log_usage(batch_id: str, model: str, input_tokens: int, output_tokens: int) -> None:
def get_daily_spend() -> float:
def check_daily_budget(limit: float = 2.0) -> bool:
    """Returns True if under budget, False if over."""
def get_monthly_summary() -> dict:
```

---

## Claude API Details

### Analysis (Haiku): `claude-haiku-4-5-20251001`

System prompt: output of `build_system_prompt(profile)`.

User prompt:
```
Analyze these {N} Dutch rental listings against the user profile.
Return ONLY a JSON array of {N} objects. No markdown, no explanation.

Per-listing schema:
{
  "index": int,
  "score": int (1-10),
  "compatible": bool,
  "confidence": "high|medium|low",
  "rejection_reason": string or null,
  "listing": {
    "address": string, "city": string, "rent_per_month": int|null,
    "size_m2": int|null, "rooms": int|null,
    "furnished_status": "kaal|gestoffeerd|gemeubileerd|unknown",
    "available_from": string|null, "energy_label": string|null,
    "contact_email": string|null, "application_url": string|null
  },
  "income_check": { "required_income": int, "user_income": int, "passes": bool },
  "trade_offs": [string],
  "recommendation": string,
  "expat_flags": [string]
}

Scoring: 9-10 perfect, 7-8 good, 5-6 acceptable, 3-4 poor, 1-2 incompatible.

Listings:
[{"index": 0, "url": "...", "text": "..."}, ...]
```

Retry: 3 attempts, exponential backoff (`time.sleep(2 ** attempt)`).

### Letters (Sonnet): `claude-sonnet-4-20250514`

System prompt: same `build_system_prompt(profile)`.

User prompt:
```
Write a professional rental application motivation letter in {language}
for the listing at {address}, {city} at EUR {rent}/month.
Ready to copy-paste. Address formally. Reference the specific property.
Listing details: {verdict JSON}
```

---

## Enriched Telegram Message Format

```
--- Score: 8/10 ---
🏠 *Keizersgracht 42, Amsterdam*
💶 €1,450/month · 65m² · 2 rooms
🛋 Gestoffeerd · Available 1 May 2026
⚡ Energy: A

_Strong match. Walking distance to your office, within budget._

Trade-offs:
\+ Prime canal location, 15 min bike to work
\+ Income check passes (€4,350 required, you earn €X)
\- No parking included
\- Short initial contract (12 months)

⚠️ Requires gemeente inschrijving (you have this)

[View Listing]  [📝 Letter NL]  [📝 Letter EN]  [Apply]
```

Listings scored below 5: compact grouped message, no inline keyboards.

---

## What NOT to Touch

- Existing parser logic in `parser.py`
- Existing `broadcast()` in `scraper.py`
- Existing subscriber filter system
- Existing bot commands and their behavior
- Polling interval and scheduler
- The web app
- `hestia_utils/secrets.py` structure

---

## Cost Budget

- Haiku analysis: ~$0.04 per batch of 20 listings
- 6 batches/day = ~$0.24/day
- Sonnet letters: ~$0.02 per letter
- ~10 auto letters/day = ~$0.20/day
- **Total: ~$0.50/day** (budget guard set at $2.00/day)
