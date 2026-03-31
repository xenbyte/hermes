# Agent 1: Database Migration + Core Data Layer

Read `@plans/context.md` first for full project context.

## Your Job

Build the foundation that all other agents depend on:
1. SQL migration files for the 4 new tables
2. `hestia/enrichment/profile.py` — profile DB access + Claude system prompt builder
3. `hestia/enrichment/queue.py` — enrichment queue DB operations
4. `hestia/enrichment/costs.py` — LLM token tracking + budget guard
5. `hestia/enrichment/__init__.py` — empty init

## Files to Read First

- `hestia/hestia_utils/db.py` — Follow the exact same patterns: `get_connection()`, `fetch_one()`, `fetch_all()`, `_write()`. Use `psycopg2` with `RealDictCursor`. Never bare `except`.
- `hestia/hestia_utils/secrets.py` (template at `misc/secrets.py.template`) — understand DB connection config.
- `misc/hestia.ddl` — existing schema for reference.

## Files to Create

### 1. `misc/sql/enrichment_schema.sql`

Contains all 4 `CREATE TABLE` statements from the context doc:
- `hestia.user_profiles`
- `hestia.enrichment_queue`
- `hestia.enrichment_results`
- `hestia.llm_usage`

Add an index on `enrichment_queue(status, enqueued_at)` for the drain query.
Add an index on `enrichment_results(profile_id, score)` for filtered lookups.
Add an index on `llm_usage(called_at)` for daily spend queries.

Use `IF NOT EXISTS` on all `CREATE TABLE` statements.

### 2. `hestia/enrichment/__init__.py`

Empty file.

### 3. `hestia/enrichment/profile.py`

Follow `db.py` patterns exactly: import `get_connection` from `hestia_utils.db`, use `RealDictCursor`.

```python
def get_profiles_with_enrichment() -> list[dict]:
    """All profiles that have max_rent and target_cities set."""

def get_profile_by_id(profile_id: int) -> dict | None:
    """Single profile by PK."""

def get_profile_for_telegram_id(telegram_id: str) -> dict | None:
    """Profile by telegram_id, or None."""

def upsert_profile(telegram_id: str, fields: dict) -> int:
    """Insert or update profile. Return profile id.
    Uses INSERT ... ON CONFLICT (telegram_id) DO UPDATE.
    Always sets updated_at = now() on update."""

def build_system_prompt(profile: dict) -> str:
    """Render profile into a structured text block for Claude's system prompt.
    Include all fields that are non-null. Format:

    You are analyzing Dutch rental listings for the following applicant:

    Name: {full_name}
    Age: {age}
    Nationality: {nationality}
    ...
    Maximum rent: EUR {max_rent}/month
    Target cities: {', '.join(target_cities)}
    ...

    Use this profile to score compatibility and write motivation letters.
    """
```

### 4. `hestia/enrichment/queue.py`

```python
import hashlib
from hestia_utils.db import get_connection, fetch_one, fetch_all, _write

def _make_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()

def enqueue(home, profile_id: int) -> bool:
    """Insert into enrichment_queue. Return False if duplicate (same url hash + profile_id combo).
    Use INSERT ... ON CONFLICT DO NOTHING and check rowcount."""

def drain_pending(limit: int = 50) -> list[dict]:
    """SELECT where status='pending' ORDER BY enqueued_at LIMIT {limit}.
    Then UPDATE those rows to status='processing' in the same transaction.
    Return the selected rows."""

def mark_done(queue_id: str) -> None:
    """Set status='done'."""

def mark_failed(queue_id: str, reason: str) -> None:
    """Set status='failed'. Could store reason in a column or log it."""

def increment_retry(queue_id: str) -> None:
    """Increment retry_count, reset status to 'pending'."""

def update_page_text(queue_id: str, text: str, method: str) -> None:
    """Store fetched page text and fetch method on the queue row."""
```

Important: `drain_pending()` must be atomic — SELECT + UPDATE in one transaction to prevent race conditions if two analyzer instances run.

### 5. `hestia/enrichment/costs.py`

```python
HAIKU_INPUT_COST_PER_M = 0.80    # $/million tokens
HAIKU_OUTPUT_COST_PER_M = 4.00
SONNET_INPUT_COST_PER_M = 3.00
SONNET_OUTPUT_COST_PER_M = 15.00

MODEL_COSTS = {
    "claude-haiku-4-5-20251001": (HAIKU_INPUT_COST_PER_M, HAIKU_OUTPUT_COST_PER_M),
    "claude-sonnet-4-20250514": (SONNET_INPUT_COST_PER_M, SONNET_OUTPUT_COST_PER_M),
}

def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in dollars."""

def log_usage(batch_id: str, model: str, input_tokens: int, output_tokens: int) -> None:
    """INSERT into hestia.llm_usage with estimated_cost."""

def get_daily_spend() -> float:
    """SUM(estimated_cost) FROM llm_usage WHERE called_at >= today midnight."""

def check_daily_budget(limit: float = 2.0) -> bool:
    """Return True if under budget."""

def get_monthly_summary() -> dict:
    """Return {'total_cost': float, 'total_calls': int, 'by_model': {...}}
    for the current calendar month."""
```

## Rules

- Follow `db.py` patterns exactly. Use `get_connection()`, close in `finally`.
- All logging via Python `logging` module, level WARNING for important events, ERROR for failures.
- No bare `except`. Catch specific exceptions.
- Type hints on all functions.
- No new dependencies needed — only `psycopg2` (already installed) and stdlib.

## How to Verify

After creating the files:
1. The SQL file should be valid PostgreSQL syntax (mentally verify or note any issues).
2. All functions in profile.py, queue.py, costs.py should import cleanly from each other.
3. `drain_pending()` must use a single transaction for SELECT + UPDATE.
4. `build_system_prompt()` should produce readable, structured text.
