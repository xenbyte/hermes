import hashlib
import psycopg2
import logging
import json
import time
from telegram import Chat
from typing import Literal
from datetime import datetime
from psycopg2.extras import RealDictCursor, RealDictRow

from hermes_utils.secrets import DB

logger = logging.getLogger(__name__)

LANG_CACHE = {}


def get_connection():
    return psycopg2.connect(database=DB["database"],
                            host=DB["host"],
                            user=DB["user"],
                            password=DB["password"],
                            port=DB["port"])


### Read actions

def fetch_one(query: str, params: list[str] = []) -> dict:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            result = cur.fetchone()
    except Exception as e:
        logger.error("Database read error: %r\nQuery: %s\nParams: %s", e, query, params)
        result = {}
    finally:
        if conn: conn.close()
    if not result:
        return {}
    return result

def fetch_all(query: str, params: list[str] = []) -> list[RealDictRow]:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            result = cur.fetchall()
    except Exception as e:
        logger.error("Database read error: %r\nQuery: %s\nParams: %s", e, query, params)
        result = []
    finally:
        if conn: conn.close()
    return result

def get_dev_mode() -> bool:
    result = fetch_one("SELECT devmode_enabled FROM hermes.meta WHERE id = 'default'")
    if result and "devmode_enabled" in result:
        return result["devmode_enabled"]
    return True
def get_scraper_halted() -> bool:
    result = fetch_one("SELECT scraper_halted FROM hermes.meta WHERE id = 'default'")
    if result and "scraper_halted" in result:
        return result["scraper_halted"]
    return True
def get_donation_link() -> str:
    result = fetch_one("SELECT donation_link FROM hermes.meta WHERE id = 'default'")
    if result and "donation_link" in result:
        return result["donation_link"]
    return ""
def get_donation_link_updated() -> datetime:
    result = fetch_one("SELECT donation_link_updated FROM hermes.meta WHERE id = 'default'")
    if result and "donation_link_updated" in result:
        return result["donation_link_updated"]
    return datetime.min

def get_user_lang(telegram_id: int) -> Literal["en", "nl"]:
    if telegram_id in LANG_CACHE:
        return LANG_CACHE[telegram_id]
    result = fetch_one("SELECT lang FROM hermes.subscribers WHERE telegram_id = %s", [str(telegram_id)])
    if result and "lang" in result and result["lang"] in ["en", "nl"]:
        LANG_CACHE[telegram_id] = result["lang"]
        return result["lang"]
    return "en"


### Write actions

def _write(query: str, params: list[str] = []) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            conn.commit()
    except Exception as e:
        logger.error("Database write error: %r\nQuery: %s\nParams: %s", e, query, params)
    finally:
        if conn: conn.close()

def add_home(url: str, address: str, city: str, price: int, agency: str, date_added: str, sqm: int = -1) -> None:
    logger.debug("add_home: %s, %s (%s) €%s", address, city, agency, price)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:32]
    _write(
        "INSERT INTO hermes.homes (url, url_hash, address, city, price, agency, date_added, sqm) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [url, url_hash, address, city, str(price), agency, date_added, str(sqm)],
    )


def get_home_by_hash(url_hash: str) -> dict:
    return fetch_one("SELECT * FROM hermes.homes WHERE url_hash = %s", [url_hash])
def add_user(telegram_id: int) -> None:
    # Auto-approve on registration — no manual approval gate.
    _write(
        "INSERT INTO hermes.subscribers (telegram_enabled, telegram_id, approved) VALUES (true, %s, true)",
        [str(telegram_id)],
    )


def get_daily_analysis_count(profile_id: int) -> int:
    """Count fresh (non-cached) analyses run today for this profile."""
    result = fetch_one(
        "SELECT COUNT(*) FROM hermes.listing_analysis "
        "WHERE profile_id = %s AND created_at >= CURRENT_DATE",
        [profile_id],
    )
    return int(result.get("count", 0))


def get_analysis_limit(telegram_id: int) -> int:
    """-1 means unlimited."""
    result = fetch_one(
        "SELECT daily_analysis_limit FROM hermes.subscribers WHERE telegram_id = %s",
        [str(telegram_id)],
    )
    return int(result.get("daily_analysis_limit", 3)) if result else 3


def promote_user(telegram_id: int) -> None:
    """Grant unlimited AI analyses."""
    _write(
        "UPDATE hermes.subscribers SET daily_analysis_limit = -1 WHERE telegram_id = %s",
        [str(telegram_id)],
    )


def update_user_identity(telegram_id: int, username: str | None, first_name: str | None) -> None:
    """Keep Telegram username and first_name fresh on every interaction."""
    _write(
        "UPDATE hermes.subscribers SET tg_username = %s, tg_first_name = %s WHERE telegram_id = %s",
        [username, first_name, str(telegram_id)],
    )


def set_analysis_limit(telegram_id: int, limit: int) -> None:
    _write(
        "UPDATE hermes.subscribers SET daily_analysis_limit = %s WHERE telegram_id = %s",
        [limit, str(telegram_id)],
    )


def get_default_analysis_limit() -> int:
    result = fetch_one("SELECT default_analysis_limit FROM hermes.meta WHERE id = 'default'")
    if result and result.get("default_analysis_limit") is not None:
        return int(result["default_analysis_limit"])
    return 3


def set_default_analysis_limit(limit: int) -> None:
    _write("UPDATE hermes.meta SET default_analysis_limit = %s WHERE id = 'default'", [limit])


def get_all_subscribers_with_usage() -> list:
    """All subscribers with today's and total analysis counts."""
    return fetch_all("""
        SELECT
            s.telegram_id, s.user_level, s.daily_analysis_limit,
            s.telegram_enabled, s.date_added, s.tg_username, s.tg_first_name,
            COALESCE(today.cnt, 0) AS today_count,
            COALESCE(total.cnt, 0) AS total_count
        FROM hermes.subscribers s
        LEFT JOIN hermes.user_profiles up ON s.telegram_id::bigint = up.telegram_id::bigint
        LEFT JOIN (
            SELECT profile_id, COUNT(*) AS cnt
            FROM hermes.listing_analysis
            WHERE created_at >= CURRENT_DATE
            GROUP BY profile_id
        ) today ON today.profile_id = up.id
        LEFT JOIN (
            SELECT profile_id, COUNT(*) AS cnt
            FROM hermes.listing_analysis
            GROUP BY profile_id
        ) total ON total.profile_id = up.id
        ORDER BY s.date_added DESC
    """)


def get_user_analysis_history(telegram_id: int) -> list:
    """Last 14 days of analysis run counts — no content, just dates and counts."""
    return fetch_all("""
        SELECT DATE(la.created_at) AS day, COUNT(*) AS count
        FROM hermes.listing_analysis la
        JOIN hermes.user_profiles up ON la.profile_id = up.id
        WHERE up.telegram_id = %s
        GROUP BY day
        ORDER BY day DESC
        LIMIT 14
    """, [str(telegram_id)])


def enable_user(telegram_id: int) -> None:
    _write("UPDATE hermes.subscribers SET telegram_enabled = true WHERE telegram_id = %s", [str(telegram_id)])
def disable_user(telegram_id: int) -> None:
    _write("UPDATE hermes.subscribers SET telegram_enabled = false WHERE telegram_id = %s", [str(telegram_id)])
def clear_apns_token(subscriber_id: int) -> None:
    _write("UPDATE hermes.subscribers SET apns_token = NULL WHERE id = %s", [str(subscriber_id)])
def halt_scraper() -> None:
    _write("UPDATE hermes.meta SET scraper_halted = true WHERE id = 'default'")
def resume_scraper() -> None:
    _write("UPDATE hermes.meta SET scraper_halted = false WHERE id = 'default'")
def enable_dev_mode() -> None:
    _write("UPDATE hermes.meta SET devmode_enabled = true WHERE id = 'default'")
def disable_dev_mode() -> None:
    _write("UPDATE hermes.meta SET devmode_enabled = false WHERE id = 'default'")
def update_donation_link(link: str) -> None:
    _write("UPDATE hermes.meta SET donation_link = %s, donation_link_updated = now() WHERE id = 'default'", [link])


def upsert_error_rollup(
    fingerprint: str,
    component: str,
    agency: str,
    target_id: int,
    error_class: str,
    message: str,
    sample: str,
    context: dict | None = None,
) -> None:
    _write(
        """
        INSERT INTO hermes.error_rollups
            (day, fingerprint, component, agency, target_id, error_class, message, sample, context, count, first_seen, last_seen)
        VALUES
            (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, 1, now(), now())
        ON CONFLICT (day, fingerprint)
        DO UPDATE SET
            count = hermes.error_rollups.count + 1,
            last_seen = now(),
            message = EXCLUDED.message,
            sample = EXCLUDED.sample,
            context = EXCLUDED.context
        """,
        [
            fingerprint,
            component,
            agency,
            target_id,
            error_class,
            message,
            sample,
            json.dumps(context or {}),
        ],
    )


def get_recent_error_rollups(hours: int = 24, limit: int = 20) -> list[RealDictRow]:
    return fetch_all(
        """
        SELECT
            fingerprint,
            component,
            agency,
            target_id,
            error_class,
            MAX(message) AS message,
            SUM(count) AS total_count,
            MIN(first_seen) AS first_seen,
            MAX(last_seen) AS last_seen
        FROM hermes.error_rollups
        WHERE last_seen >= now() - (%s::int * interval '1 hour')
        GROUP BY fingerprint, component, agency, target_id, error_class
        HAVING SUM(count) > 50
        ORDER BY total_count DESC, MAX(last_seen) DESC
        LIMIT %s
        """,
        [hours, limit],
    )


def cleanup_error_rollups(retention_days: int = 30) -> None:
    _write(
        "DELETE FROM hermes.error_rollups WHERE day < CURRENT_DATE - %s::int",
        [retention_days],
    )


_AGENCY_CONFIG_CACHE: dict[str, tuple[dict, float]] = {}
_AGENCY_CONFIG_TTL = 300  # seconds


def get_agency_detail_config(agency: str) -> dict:
    """Return {ai_analysis_enabled: bool, detail_fetch_method: str} for an agency.

    Result is cached for 5 minutes to avoid per-request DB hits.
    Falls back to disabled/http if the agency is not found in targets.
    """
    now = time.monotonic()
    cached = _AGENCY_CONFIG_CACHE.get(agency)
    if cached and now - cached[1] < _AGENCY_CONFIG_TTL:
        return cached[0]

    row = fetch_one(
        "SELECT ai_analysis_enabled, detail_fetch_method FROM hermes.targets WHERE agency = %s LIMIT 1",
        [agency],
    )
    config = {
        "ai_analysis_enabled": bool(row.get("ai_analysis_enabled", False)),
        "detail_fetch_method": row.get("detail_fetch_method", "http") or "http",
    }
    _AGENCY_CONFIG_CACHE[agency] = (config, now)
    return config


def get_enabled_targets_without_recent_homes(days: int = 7) -> list[RealDictRow]:
    return fetch_all(
        """
        SELECT
            t.id,
            t.agency,
            COUNT(h.url) AS homes_count
        FROM hermes.targets t
        LEFT JOIN hermes.homes h
            ON h.agency = t.agency
           AND h.date_added >= now() - (%s::int * interval '1 day')
        WHERE t.enabled = true
        GROUP BY t.id, t.agency
        HAVING COUNT(h.url) = 0
        ORDER BY t.id
        """,
        [days],
    )

def approve_user(telegram_id: int) -> None:
    _write("UPDATE hermes.subscribers SET approved = true WHERE telegram_id = %s", [str(telegram_id)])

def deny_user(telegram_id: int) -> None:
    _write("DELETE FROM hermes.subscribers WHERE telegram_id = %s AND approved = false", [str(telegram_id)])

def get_pending_users() -> list[RealDictRow]:
    return fetch_all("SELECT id, telegram_id, date_added FROM hermes.subscribers WHERE approved = false ORDER BY date_added")

def is_user_approved(telegram_id: int) -> bool:
    result = fetch_one("SELECT approved FROM hermes.subscribers WHERE telegram_id = %s", [str(telegram_id)])
    return bool(result and result.get("approved"))


def set_filter_minprice(telegram_chat: Chat, min_price: int) -> None:
    _write("UPDATE hermes.subscribers SET filter_min_price = %s WHERE telegram_id = %s", [str(min_price), str(telegram_chat.id)])
def set_filter_maxprice(telegram_chat: Chat, max_price: int) -> None:
    _write("UPDATE hermes.subscribers SET filter_max_price = %s WHERE telegram_id = %s", [str(max_price), str(telegram_chat.id)])
def set_filter_cities(telegram_chat: Chat, cities: str) -> None:
    _write("UPDATE hermes.subscribers SET filter_cities = %s WHERE telegram_id = %s", [str(cities).replace("'", '"'), str(telegram_chat.id)])
def set_filter_agencies(telegram_chat: Chat, agencies: set[str]) -> None:
    _write("UPDATE hermes.subscribers SET filter_agencies = %s WHERE telegram_id = %s", [str(list(agencies)).replace("'", '"'), str(telegram_chat.id)])
def set_filter_minsqm(telegram_chat: Chat, min_sqm: int) -> None:
    _write("UPDATE hermes.subscribers SET filter_min_sqm = %s WHERE telegram_id = %s", [str(min_sqm), str(telegram_chat.id)])

def set_user_lang(telegram_chat: Chat, lang: Literal["en", "nl"]) -> None:
    _write("UPDATE hermes.subscribers SET lang = %s WHERE telegram_id = %s", [lang, str(telegram_chat.id)])
    LANG_CACHE[telegram_chat.id] = lang


FILTER_COLUMNS = ["filter_min_price", "filter_max_price", "filter_cities", "filter_agencies", "filter_min_sqm"]


def _load_filter_defaults(cur) -> dict:
    cur.execute(
        """
        SELECT column_name, column_default, data_type
        FROM information_schema.columns
        WHERE table_schema = 'hermes'
          AND table_name = 'subscribers'
          AND column_name = ANY(%s)
        """,
        [FILTER_COLUMNS],
    )
    defaults = {
        row["column_name"]: {
            "default": row["column_default"],
            "type": row["data_type"],
        }
        for row in cur.fetchall()
    }
    for col in FILTER_COLUMNS:
        if col not in defaults or defaults[col]["default"] is None:
            defaults[col] = {"default": "NULL", "type": "unknown"}
    return defaults


def _filters_are_default(cur, where_sql: str, params: list[str], defaults: dict) -> bool:
    checks = []
    for col in FILTER_COLUMNS:
        default_expr = defaults[col]["default"]
        col_type = defaults[col]["type"]
        if col_type in ["json", "jsonb"]:
            checks.append(f"({col})::text IS NOT DISTINCT FROM ({default_expr})::text AS {col}_default")
        else:
            checks.append(f"{col} IS NOT DISTINCT FROM {default_expr} AS {col}_default")
    query = f"SELECT {', '.join(checks)} FROM hermes.subscribers WHERE {where_sql}"
    cur.execute(query, params)
    result = cur.fetchone()
    if not result:
        return False
    return all(result.values())


def link_account(telegram_id: int, code: str) -> Literal["success", "invalid_code", "already_linked"]:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Look up the code
            cur.execute("SELECT email_address FROM hermes.link_codes WHERE code = %s AND expires_at > now()", [code])
            row = cur.fetchone()
            if not row:
                return "invalid_code"

            email = row["email_address"]

            # Check if the Telegram user already has an email linked
            cur.execute("SELECT email_address FROM hermes.subscribers WHERE telegram_id = %s", [str(telegram_id)])
            sub = cur.fetchone()
            if sub and sub["email_address"]:
                return "already_linked"

            # Read both subscriber rows and compare filters with DB defaults
            cur.execute("SELECT * FROM hermes.subscribers WHERE telegram_id = %s", [str(telegram_id)])
            tg_sub = cur.fetchone()
            cur.execute("SELECT * FROM hermes.subscribers WHERE email_address = %s AND telegram_id IS NULL", [email])
            web_sub = cur.fetchone()

            defaults = _load_filter_defaults(cur)
            tg_default = _filters_are_default(cur, "telegram_id = %s", [str(telegram_id)], defaults)
            web_default = True
            if web_sub:
                web_default = _filters_are_default(cur, "email_address = %s AND telegram_id IS NULL", [email], defaults)

            # Overwrite the default filters with the non-default filters
            if tg_sub and web_sub and tg_default and not web_default:
                cur.execute(
                    """
                    UPDATE hermes.subscribers
                    SET filter_min_price = %s,
                        filter_max_price = %s,
                        filter_cities = %s,
                        filter_agencies = %s,
                        filter_min_sqm = %s
                    WHERE telegram_id = %s
                    """,
                    [
                        web_sub["filter_min_price"],
                        web_sub["filter_max_price"],
                        web_sub["filter_cities"],
                        web_sub["filter_agencies"],
                        web_sub["filter_min_sqm"],
                        str(telegram_id),
                    ],
                )

            # Set email on the Telegram user's row
            cur.execute("UPDATE hermes.subscribers SET email_address = %s WHERE telegram_id = %s", [email, str(telegram_id)])

            # Delete the web-only subscriber row (has email but no telegram_id)
            cur.execute("DELETE FROM hermes.subscribers WHERE email_address = %s AND telegram_id IS NULL", [email])

            # Delete the used link code
            cur.execute("DELETE FROM hermes.link_codes WHERE code = %s", [code])

            conn.commit()
            return "success"
    except Exception as e:
        conn.rollback()
        logger.error("Database error in link_account: %r", e)
        return "invalid_code"
    finally:
        if conn: conn.close()
