# Hermes — Claude Code Guide

This file is the single source of truth for AI-assisted development on this project. Read it before making any changes.

---

## What This Project Is

Hermes is a Dutch rental listing aggregator. It scrapes 20+ rental websites every 5 minutes, sends Telegram notifications for new listings that match each subscriber's filters, and optionally enriches listings with Claude AI analysis (scoring, trade-offs, motivation letters).

Fork of [wtfloris/hestia](https://github.com/wtfloris/hestia), extended with AI enrichment, a web dashboard, and an access approval system.

---

## Architecture

Five Docker containers, one shared PostgreSQL database (`hermes` schema):

| Service | Source | Purpose |
|---|---|---|
| `hermes-bot` | `hermes/bot.py` | Long-polling Telegram bot; handles all user commands and callbacks |
| `hermes-scraper` | `hermes/scraper.py` | Cron every 5 min; scrapes targets, writes `homes`, broadcasts to subscribers |
| `hermes-analyzer` | `hermes/enrichment/analyzer.py` | Cron every N hours; drains enrichment queue, calls Claude, sends enriched messages |
| `hermes-web` | `web/hermes_web/app.py` | Flask dashboard on port 19191; filter management, magic-link email auth |
| `hermes-database` | PostgreSQL 16 | All persistent state |

All services write logs to `docker/data/hermes.log` (mounted as `/data/hermes.log` inside containers).

---

## Repository Layout

```
hermes/
├── hermes/                     # Bot + scraper + analyzer
│   ├── bot.py                  # Telegram command handlers, callback_query_handler()
│   ├── scraper.py              # scrape_site(), broadcast(), enqueue_for_enrichment()
│   ├── cli.py                  # Admin CLI: list/approve/deny access requests
│   ├── hermes_utils/
│   │   ├── db.py               # All PostgreSQL access (fetch_one, fetch_all, _write)
│   │   ├── parser.py           # Home class + 25+ site parsers in HomeResults
│   │   ├── meta.py             # Shared BOT instance, emoji constants, escape_markdownv2()
│   │   ├── strings.py          # i18n strings (en/nl)
│   │   ├── logging_config.py   # Centralized file logging
│   │   ├── apns.py             # Apple Push Notification Service
│   │   └── secrets.py          # NOT committed — TOKEN, DB, APNS
│   ├── enrichment/
│   │   ├── analyzer.py         # run_analysis() — main cron entry point
│   │   ├── profile.py          # get_profiles_with_enrichment(), build_system_prompt()
│   │   ├── prefilter.py        # should_enqueue(), enqueue_for_enrichment()
│   │   ├── queue.py            # enqueue(), drain_pending(), mark_done/failed()
│   │   ├── fetcher.py          # fetch_detail_page() with HTTP + Playwright fallback
│   │   ├── letters.py          # generate_letter() via Claude Sonnet, with cache
│   │   └── costs.py            # log_usage(), check_daily_budget(), get_daily_spend()
│   └── requirements.txt
├── web/
│   ├── hermes_web/app.py       # Flask app
│   ├── templates/              # Jinja2 HTML templates
│   ├── static/                 # CSS/JS assets
│   └── requirements.txt
├── docker/
│   ├── docker-compose.yml      # Production stack
│   ├── docker-compose-dev.yml  # Dev overrides
│   ├── .env                    # Environment variables (not committed)
│   ├── Dockerfile.bot / .scraper / .analyzer
│   ├── init-db/01-init.sql     # Schema bootstrap on first start
│   └── data/                   # Persistent log files (gitignored)
├── tests/                      # pytest test suite
│   ├── conftest.py
│   ├── test_db.py
│   ├── test_enrichment.py
│   ├── test_home.py
│   ├── test_parsers.py         # Most comprehensive — 60KB
│   ├── test_scraper.py
│   ├── test_meta.py
│   └── test_strings.py
├── web/tests/test_app.py
├── misc/
│   ├── hermes.ddl              # Full DB schema DDL
│   ├── sql/                    # Migration scripts (must be encrypted as .sql.enc)
│   └── secrets.py.template     # Template for hermes_utils/secrets.py
├── plans/                      # Development context docs (keep up to date)
│   └── context.md              # Full enrichment architecture context
├── build.sh                    # Build + deploy script
└── README.md
```

---

## Database Schema

All tables in the `hermes` schema. Core tables:

| Table | Key Columns |
|---|---|
| `homes` | `url`, `address`, `city`, `price`, `sqm`, `agency`, `date_added` |
| `subscribers` | `telegram_id`, `email_address`, `user_level` (0=user, 9=admin), `approved`, `filter_*`, `lang` |
| `targets` | `agency`, `queryurl`, `method`, `post_data`, `headers`, `enabled` |
| `meta` | `devmode_enabled`, `scraper_halted`, `workdir`, `donation_link` |
| `preview_cache` | `url`, `status`, `image_url`, `expires_at` |
| `magic_tokens` | `token_id`, `email_address`, `expires_at` |
| `link_codes` | `code`, `email_address`, `expires_at` |
| `error_rollups` | `day`, `fingerprint`, `component`, `agency`, `error_class`, `count` |
| `user_profiles` | `telegram_id`, `full_name`, `max_rent`, `target_cities`, `employer`, `gross_monthly_income`, … |
| `enrichment_queue` | `id` (sha256 url), `profile_id`, `url`, `status` (pending/processing/done/failed) |
| `enrichment_results` | `id`, `score` (1–10), `compatible`, `trade_offs`, `letter_nl`, `letter_en` |
| `llm_usage` | `model`, `input_tokens`, `output_tokens`, `estimated_cost`, `called_at` |

---

## Environment Variables

**`docker/.env`** (not committed):

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key — required for analyzer service |
| `DATABASE_URL` | PostgreSQL DSN — default: `postgresql://hermes:hermes@hermes-database:5432/hermes` |
| `SECRET_KEY` | Flask session secret — must be changed in production |
| `BREVO_API_KEY` | Email service for web dashboard magic links (optional) |
| `FROM_EMAIL` | Sender email address |
| `BASE_URL` | Public URL of web dashboard |
| `ENRICHMENT_INTERVAL_HOURS` | How often analyzer runs (default: 4) |
| `LOG_LEVEL` | Logging level: DEBUG / INFO / WARNING / ERROR (default: INFO) |

**`hermes/hermes_utils/secrets.py`** (not committed, copy from `misc/secrets.py.template`):

| Variable | Purpose |
|---|---|
| `TOKEN` | Telegram bot token |
| `DB` | Dict with PostgreSQL credentials |

---

## Common Commands

```bash
# Run all tests
python -m pytest -q

# Run tests for a specific module
python -m pytest tests/test_parsers.py -q

# Build and start full stack (production)
bash build.sh -y

# Build and start dev stack
bash build.sh dev -y

# Check running containers
docker compose -f docker/docker-compose.yml ps

# Tail logs
docker logs -f hermes-bot
docker logs -f hermes-scraper
docker logs -f hermes-analyzer

# Connect to database
docker exec -it hermes-database psql -U hermes -d hermes

# Manage user access requests
python hermes/cli.py list
python hermes/cli.py approve <telegram_id>
python hermes/cli.py deny <telegram_id>
```

---

## Claude API Usage in This Project

Two models are used in `hermes/enrichment/`:

| Use case | Model | File |
|---|---|---|
| Listing analysis (batch scoring) | `claude-haiku-4-5-20251001` | `analyzer.py` |
| Motivation letter generation | `claude-sonnet-4-6` | `letters.py` |

Daily budget guard: `$2.00/day`. Checked in `costs.py` before each analyzer run.

When updating Claude model identifiers, use the latest available:
- Haiku: `claude-haiku-4-5-20251001`
- Sonnet: `claude-sonnet-4-6`
- Opus: `claude-opus-4-6`

---

## Code Conventions

- **Python 3.11+**. Type hints encouraged for new functions.
- **DB access:** Always go through `hermes_utils/db.py` helpers (`fetch_one`, `fetch_all`, `_write`). Never open raw connections elsewhere.
- **Async:** The bot is async (`python-telegram-bot`). The scraper uses `asyncio.run()`. New bot handlers must be `async def`.
- **Telegram messages:** Always use MarkdownV2 format. Use `escape_markdownv2()` from `meta.py` for user-supplied strings.
- **Callback data format:** Existing bot callbacks use `.` as separator (e.g. `hfa.d.{agency}`). Enrichment callbacks use `:` (e.g. `letter_nl:{id}`).
- **Logging:** Use `logging.getLogger(__name__)` in every module. Never use `print()` in production code.
- **SQL migrations:** New SQL files go in `misc/sql/` and must be encrypted with sops before committing (`*.sql.enc`). The CI/CD pipeline enforces this.
- **Secrets:** Never hardcode credentials. `secrets.py` is gitignored. Use environment variables for Docker services.

---

## What NOT to Touch

Unless explicitly asked:

- `hermes/hermes_utils/parser.py` — parser logic for 25+ agencies is fragile
- `broadcast()` in `scraper.py` — existing subscriber notification pipeline
- Existing subscriber filter system (price, city, agency, sqm)
- Existing bot commands and their behavior
- Web app (`web/`) unless the task is specifically web-related
- `hermes_utils/secrets.py` structure

---

## Testing

- **Framework:** `pytest` with `pytest-asyncio`
- Tests do NOT mock the database by default — use a real test DB where needed
- Parser tests are comprehensive (`test_parsers.py` — 60KB); run them after any scraper changes
- CI runs on every PR via `.github/workflows/ci.yml`

---

## Rules for Claude Code

### Committing

After completing any code task:
1. Stage only the files changed for the task (avoid `git add .` unless all changes are intentional)
2. Write a concise commit message focused on *why*, not *what*
3. Create the commit without asking for confirmation, unless the changes are destructive or affect shared infrastructure

### Keeping Docs Up to Date

Update this file (`CLAUDE.md`) whenever:
- New services or Docker containers are added
- New tables are added to the database schema
- New environment variables are introduced
- Architectural patterns or conventions change
- New key files are added that future agents need to know about

Update `docker/README.md` whenever:
- Deployment steps change
- New environment variables are added
- New admin commands are added
- Service list changes

Update `README.md` whenever:
- User-facing features are added or removed
- High-level project description changes

### General Behavior

- Read files before modifying them. Never guess at file contents.
- When adding a new feature, check if a similar pattern already exists in the codebase and follow it.
- Do not add unnecessary abstractions. If something works in 3 lines, don't create a utility class for it.
- If a task touches the enrichment pipeline, re-read `plans/context.md` for the full design intent.
- Always run tests after making code changes: `python -m pytest -q`
