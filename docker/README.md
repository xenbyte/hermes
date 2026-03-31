## Deployment Guide

### Prerequisites

- Docker and Docker Compose installed
- A Telegram bot token (create one via [@BotFather](https://t.me/BotFather))
- (Optional) An [Anthropic API key](https://console.anthropic.com/) for AI listing analysis
- (Optional) A [Brevo](https://www.brevo.com/) API key for the web dashboard email login

### 1. Configure secrets

**`hermes/hermes_utils/secrets.py`** — Copy the template and fill in your values:

```bash
cp misc/secrets.py.template hermes/hermes_utils/secrets.py
```

Edit the file and set:

| Field | Description |
|-------|-------------|
| `OWN_CHAT_ID` | Your personal Telegram chat ID (integer). Send `/start` to [@userinfobot](https://t.me/userinfobot) to find it. |
| `PRIVILEGED_USERS` | List of Telegram user IDs that can use admin commands (`/status`, `/halt`, `/announce`, etc.) |
| `TOKEN` | Your Telegram bot token from BotFather |
| `DB` | Database credentials — defaults match the Docker Compose setup, no changes needed for local deployment |

**`docker/.env`** — Environment variables for Docker Compose services:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key for AI analysis (required for the analyzer service) |
| `DATABASE_URL` | PostgreSQL connection string — default: `postgresql://hermes:hermes@hermes-database:5432/hermes` |
| `SECRET_KEY` | Flask session secret key — **change this in production** |
| `BREVO_API_KEY` | Brevo (SendinBlue) API key for sending login emails via the web dashboard |
| `FROM_EMAIL` | Sender email address for login emails |
| `BASE_URL` | Public URL of the web dashboard (e.g., `https://yourdomain.com`) |
| `ENRICHMENT_INTERVAL_HOURS` | How often the AI analyzer runs (default: `4` hours) |

### 2. Build and start

From the project root:

```bash
bash build.sh -y
```

This builds all Docker images and starts the stack. The `-y` flag skips the confirmation prompt.

For development, use:

```bash
bash build.sh dev -y
```

### 3. Verify

Check that all containers are running:

```bash
docker compose -f docker/docker-compose.yml ps
```

You should see five services:

| Service | Purpose |
|---------|---------|
| `hermes-bot` | Telegram bot — handles commands and user interaction |
| `hermes-scraper` | Scrapes rental websites every 5 minutes via cron |
| `hermes-analyzer` | AI analysis of listings (runs periodically via cron) |
| `hermes-database` | PostgreSQL database |
| `hermes-web` | Web dashboard (port `19191`) |

### 4. Start using the bot

1. Open your bot on Telegram (the one you created with BotFather)
2. Send `/start` to subscribe
3. Use `/filter` to set your price range, cities, and agencies
4. Use `/websites` to see which sites are being scraped
5. New listings matching your filters will be sent automatically

### Architecture

```
┌─────────────┐   ┌──────────────┐   ┌──────────────┐
│  Scraper     │   │  Bot         │   │  Analyzer    │
│  (cron 5min) │   │  (polling)   │   │  (cron Xh)   │
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       │                  │                   │
       └──────────┬───────┴───────────────────┘
                  │
           ┌──────▼───────┐
           │  PostgreSQL   │
           │  (hermes db)  │
           └──────┬───────┘
                  │
           ┌──────▼───────┐
           │  Web Dashboard│
           │  (port 19191) │
           └──────────────┘
```

- **Scraper** fetches listings from target websites every 5 minutes, writes new homes to the database, and triggers Telegram notifications via the Bot API.
- **Bot** handles Telegram commands (`/start`, `/stop`, `/filter`, `/profile`, etc.) and serves inline keyboards for agency filtering.
- **Analyzer** periodically drains the enrichment queue, fetches listing page content, sends it to Claude for analysis, and delivers AI verdicts to users via Telegram.
- **Web Dashboard** provides a browser-based UI for managing filters and browsing listings. Uses magic link email authentication.

### Admin commands

Users listed in `PRIVILEGED_USERS` (in `secrets.py`) with `user_level = 9` in the database can use:

| Command | Description |
|---------|-------------|
| `/status` | Show system status, subscriber count, and target health |
| `/halt` | Pause the scraper |
| `/resume` | Resume the scraper |
| `/dev` | Enable dev mode (broadcasts only reach admin users) |
| `/nodev` | Disable dev mode |
| `/announce <message>` | Broadcast a message to all subscribers |
| `/setdonate <url>` | Set the donation link |

### Logs

Logs are written to `docker/data/hermes.log` (mounted as `/data/hermes.log` inside containers). View live logs with:

```bash
docker logs -f hermes-bot
docker logs -f hermes-scraper
docker logs -f hermes-analyzer
```

### Database

The database schema is `hermes`. On first start, `docker/init-db/01-init.sql` creates all tables automatically. To connect directly:

```bash
docker exec -it hermes-database psql -U hermes -d hermes
```
