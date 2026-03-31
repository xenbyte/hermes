# Agent 4: Docker Setup + Tests

Read `@plans/context.md` first for full project context.

## Prerequisites

Agents 1, 2, and 3 must be complete. Verify all enrichment files exist:
- `hestia/enrichment/__init__.py`
- `hestia/enrichment/profile.py`
- `hestia/enrichment/queue.py`
- `hestia/enrichment/costs.py`
- `hestia/enrichment/fetcher.py`
- `hestia/enrichment/analyzer.py`
- `hestia/enrichment/letters.py`
- `hestia/enrichment/prefilter.py`
- `misc/sql/enrichment_schema.sql`

Also verify the modifications to:
- `hestia/scraper.py` (has `enqueue_for_enrichment` call)
- `hestia/bot.py` (has letter callbacks, `/profile`, `/cost`)

## Your Job

1. `docker/Dockerfile.analyzer` — Docker image for the analyzer cron job
2. Modify `docker/docker-compose.yml` — add analyzer service
3. Modify `build.sh` — add analyzer image build + SQL migration
4. `tests/test_enrichment.py` — unit tests for the enrichment modules
5. Update `tests/requirements-test.txt` if needed

## Files to Read First

- `docker/Dockerfile.scraper` — follow the same pattern for the analyzer Dockerfile (cron-based)
- `docker/Dockerfile.bot` — for comparison
- `docker/docker-compose.yml` — understand existing service definitions
- `docker/docker-compose-dev.yml` — understand dev overrides
- `build.sh` — understand the build and migration pipeline
- `tests/conftest.py` — understand test fixtures (mocked BOT, mocked secrets)
- `tests/` — all existing test files for patterns

## Files to Create

### 1. `docker/Dockerfile.analyzer`

Follow `Dockerfile.scraper` pattern closely:

```dockerfile
FROM python:3.12-slim

# Install cron
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

WORKDIR /analyzer

# Copy requirements and install
COPY hestia/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY hestia/ ./hestia/

# Create data directory for logs
RUN mkdir -p /data

# Set up cron job — default every 4 hours, configurable at build time
# The ENRICHMENT_INTERVAL_HOURS env var is read at container start
COPY docker/analyzer-cron-entry.sh /analyzer-cron-entry.sh
RUN chmod +x /analyzer-cron-entry.sh

CMD ["/analyzer-cron-entry.sh"]
```

### 2. `docker/analyzer-cron-entry.sh`

```bash
#!/bin/bash
# Generate cron schedule from ENRICHMENT_INTERVAL_HOURS (default: 4)
INTERVAL=${ENRICHMENT_INTERVAL_HOURS:-4}
echo "0 */${INTERVAL} * * * root cd /analyzer && ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY} python3 -m hestia.enrichment.analyzer >> /data/analyzer.log 2>&1" > /etc/cron.d/analyzer
chmod 0644 /etc/cron.d/analyzer
crontab /etc/cron.d/analyzer

# Run cron in foreground
cron -f
```

### 3. Modify `docker/docker-compose.yml`

Add the analyzer service alongside the existing services:

```yaml
  hestia-analyzer:
    build:
      context: ..
      dockerfile: docker/Dockerfile.analyzer
    container_name: hestia-analyzer
    restart: unless-stopped
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - ENRICHMENT_INTERVAL_HOURS=${ENRICHMENT_INTERVAL_HOURS:-4}
    volumes:
      - hestia-data:/data
    depends_on:
      - postgres
```

The `ANTHROPIC_API_KEY` comes from the host environment or a `.env` file.

### 4. Modify `build.sh`

Add the analyzer image build alongside existing bot/scraper builds:

```bash
# In the build section, add:
docker build -t hestia-analyzer -f docker/Dockerfile.analyzer .
```

Add migration for enrichment schema:

```bash
# In the migration section, add:
docker exec -i postgres psql -U hestia -d hestia < misc/sql/enrichment_schema.sql
```

Look at how existing builds and migrations are structured in `build.sh` and follow the exact same pattern.

### 5. `tests/test_enrichment.py`

```python
"""Tests for the enrichment pipeline modules."""
import pytest
import json
from unittest.mock import patch, MagicMock

# These tests must work without a real database or API keys.
# Mock all external dependencies.


class TestPrefilter:
    """Test prefilter.should_enqueue()"""

    def test_accepts_matching_listing(self):
        """Home with price under max_rent and city in target_cities passes."""

    def test_rejects_over_budget(self):
        """Home with price > max_rent is rejected."""

    def test_rejects_wrong_city(self):
        """Home in a city not in target_cities is rejected."""

    def test_city_match_case_insensitive(self):
        """City matching is case-insensitive."""

    def test_rejects_invalid_price(self):
        """Home with price <= 0 is rejected."""


class TestClaudeResponseParser:
    """Test analyzer._parse_claude_response()"""

    def test_valid_json_array(self):
        """Normal JSON array is parsed correctly."""
        # Input: '[{"index": 0, "score": 8, "compatible": true, ...}]'

    def test_markdown_fenced_json(self):
        """JSON wrapped in ```json ... ``` fences is handled."""
        # Input: '```json\n[{"index": 0, ...}]\n```'

    def test_truncated_json(self):
        """Truncated JSON returns partial results, not an error."""
        # Input: '[{"index": 0, "score": 8}, {"index": 1, "sco'

    def test_empty_string(self):
        """Empty string returns empty list."""

    def test_random_text(self):
        """Non-JSON text returns empty list, never raises."""

    def test_single_object_not_array(self):
        """A single JSON object (not array) is wrapped in a list."""


class TestCostTracker:
    """Test costs.py functions"""

    def test_estimate_cost_haiku(self):
        """Haiku cost calculation matches expected rates."""

    def test_estimate_cost_sonnet(self):
        """Sonnet cost calculation matches expected rates."""

    def test_budget_check_under_limit(self):
        """check_daily_budget returns True when under limit."""

    def test_budget_check_over_limit(self):
        """check_daily_budget returns False when over limit."""


class TestLetterCache:
    """Test letters.py caching behavior"""

    def test_returns_cached_letter(self):
        """If letter is already cached, return it without calling Claude."""

    def test_generates_and_caches(self):
        """If letter is not cached, call Claude, cache result, return it."""

    def test_handles_api_failure(self):
        """If Claude API fails, return error string, never raise."""


class TestEnrichedMessage:
    """Test the enriched Telegram message formatting"""

    def test_high_score_message_format(self):
        """Score >= 8 message includes all sections and inline keyboard."""

    def test_low_score_compact_format(self):
        """Score < 5 uses compact format."""

    def test_markdownv2_escaping(self):
        """Special characters are properly escaped for MarkdownV2."""
```

Each test class should have working implementations. Use `unittest.mock.patch` to mock:
- `hestia_utils.db.fetch_one`, `fetch_all`, `_write`, `get_connection` — always mock DB
- `anthropic.Anthropic` — always mock the API client
- `hestia_utils.meta.BOT` — already mocked in `conftest.py`

For `TestPrefilter`: Create a mock `Home` object with the right attributes. Don't import the real `Home` class if it has import side effects — create a simple mock with `address`, `city`, `price`, `url`, `agency`, `sqm` attributes.

For `TestClaudeResponseParser`: Import `_parse_claude_response` from `hestia.enrichment.analyzer` and test it directly. This is the most important test — it must handle all edge cases.

### 6. Update `tests/requirements-test.txt`

Check if it already has `pytest` and `pytest-asyncio`. If not, add them. No other test deps needed — we mock everything.

## Rules

- Follow existing Docker patterns exactly. Don't invent new conventions.
- The analyzer container must have access to the same Postgres instance as bot and scraper.
- Tests must run without any external services (no DB, no API, no Telegram).
- Tests must not import `hestia_utils.secrets` directly (it's not committed). Use mocks as shown in `tests/conftest.py`.
- The cron entry script must forward environment variables into the cron environment (cron doesn't inherit them by default — the script handles this).

## How to Verify

1. `docker build -f docker/Dockerfile.analyzer .` succeeds (if Docker is available).
2. `pytest tests/test_enrichment.py` passes with all tests green.
3. `docker-compose.yml` is valid YAML and the analyzer service references the correct Dockerfile.
4. `build.sh` builds all three images and applies the new migration.
