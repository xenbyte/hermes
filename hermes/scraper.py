import json
import logging
import hashlib
import traceback
import requests
from curl_cffi import requests as cf_requests
from collections import Counter
from time import sleep
from asyncio import run
from telegram.error import Forbidden
from datetime import datetime, timedelta

import hermes_utils.db as db
import hermes_utils.meta as meta
import hermes_utils.secrets as secrets
import hermes_utils.apns as apns
from hermes_utils.parser import Home, HomeResults
from hermes_utils.logging_config import setup_logging
from enrichment.prefilter import enqueue_for_enrichment

logger = logging.getLogger(__name__)

APNS_MAX_RETRIES = 3
APNS_RETRY_BASE_SECONDS = 0.5
APNS_INVALID_TOKEN_THRESHOLD = 1
SCRAPER_METRICS = Counter()


def _increment_scraper_metric(metric_name: str, outcome: str) -> int:
    key = f"{metric_name}:{outcome}"
    SCRAPER_METRICS[key] += 1
    value = SCRAPER_METRICS[key]
    logger.debug("scraper_metric metric=%s outcome=%s value=%s", metric_name, outcome, value)
    return value


def _build_error_fingerprint(component: str, target: dict, exc: BaseException) -> str:
    raw = "|".join(
        [
            component,
            str(target.get("id", -1)),
            str(target.get("agency", "unknown")),
            exc.__class__.__name__,
            str(exc),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _build_daily_error_digest() -> str:
    rows = db.get_recent_error_rollups(hours=24, limit=20)
    if not rows:
        return ""

    message = "\n\nError digest (past 24h):"
    for row in rows:
        message += (
            f"\n- {row['total_count']}x {row['error_class']}"
            f" [{row['agency']}:{row['target_id']}]"
            f" ({row['component']})"
        )
        short_message = str(row["message"]).replace("\n", " ").strip()
        if short_message:
            message += f"\n  {short_message[:160]}"

    return message


def _build_zero_results_digest() -> str:
    rows = db.get_enabled_targets_without_recent_homes(days=7)
    if not rows:
        return ""

    message = "\n\nEnabled targets with 0 listings in the past 7 days:"
    for row in rows:
        message += f"\n- {row['agency']} ({row['id']})"
    return message


async def _record_target_error(target: dict, exc: BaseException) -> None:
    try:
        db.upsert_error_rollup(
            fingerprint=_build_error_fingerprint("scrape_site", target, exc),
            component="scrape_site",
            agency=str(target.get("agency", "unknown")),
            target_id=int(target.get("id", 0)),
            error_class=exc.__class__.__name__,
            message=str(exc)[:400],
            sample="".join(traceback.format_exception_only(type(exc), exc)).strip()[:1000],
            context={"method": target.get("method"), "queryurl": target.get("queryurl")},
        )
    except BaseException as db_error:
        fallback_error = f"Failed to persist error rollup for target {target.get('id')}: {repr(db_error)}"
        logger.error(fallback_error)
        for admin in db.fetch_all("SELECT telegram_id FROM hermes.subscribers WHERE user_level = 9 AND telegram_enabled = true"):
            await meta.BOT.send_message(text=fallback_error, chat_id=admin["telegram_id"])


async def main() -> None:
    
    # Once a day at exactly 19:00 UTC, check some stuff and send an alert if necessary
    if datetime.now().hour == 19 and datetime.now().minute == 0:
        message = ""
        if db.get_dev_mode():
            message += "\n\nDev mode is enabled"
        if db.get_scraper_halted() and 'dev' not in meta.APP_VERSION:
            message += "\n\nScraper is halted"
    
        # Check if the donation link is expiring soon
        # Expiry of Tikkie links is 14 days, start warning after 13
        last_updated = db.get_donation_link_updated()
        if datetime.now() - last_updated >= timedelta(days=13):
            message += "\n\nDonation link expiring soon, use /setdonate"

        message += _build_daily_error_digest()
        message += _build_zero_results_digest()
        db.cleanup_error_rollups(retention_days=30)
            
        if message:
            for admin in db.fetch_all("SELECT telegram_id FROM hermes.subscribers WHERE user_level = 9 AND telegram_enabled = true"):
                await meta.BOT.send_message(text=message[2:], chat_id=admin["telegram_id"])

    # Once a week, Friday 6pm UTC, send all who subscribed three weeks ago a thanks with a donation link reminder
    if datetime.now().weekday() == 4 and datetime.now().hour == 18 and datetime.now().minute < 4:
        if db.get_dev_mode():
            logger.warning("Dev mode is enabled, not broadcasting thanks messages")
        else:
            subs = db.fetch_all("""
                SELECT * FROM hermes.subscribers 
                WHERE telegram_enabled = true 
                AND date_added BETWEEN NOW() - INTERVAL '4 weeks' AND NOW() - INTERVAL '3 weeks'
            """)

            donation_link = db.get_donation_link()
            logger.info("Broadcasting thanks message to %d subscribers", len(subs))
            for sub in subs:
                sleep(1/29)  # avoid rate limit (broadcasting to max 30 users per second)
                message = rf"""Thanks for using Hermes, I\'ve put a lot of work into it and I hope it\'s helping you out\!
                
Moving is expensive enough and similar scraping services start at like €20/month\. Hopefully Hermes has helped you save some money\! With this open Tikkie you could use some of those savings to [buy me a beer]({donation_link}) {meta.LOVE_EMOJI}

Good luck in your search\!"""
                try:
                    await meta.BOT.send_message(text=message, chat_id=sub["telegram_id"], parse_mode="MarkdownV2", disable_web_page_preview=True)
                except BaseException as e:
                    logger.warning("Exception while broadcasting thanks message to telegram_id=%s: %r", sub['telegram_id'], e)
                    continue
    
    if not db.get_scraper_halted():
        scrape_start_ts = datetime.now()
        targets = db.fetch_all("SELECT * FROM hermes.targets WHERE enabled = true")
        logger.info("Starting scrape run for %d targets", len(targets))
        for target in targets:
            try:
                await scrape_site(target)
            except BaseException as e:
                error = f"[{target['agency']} ({target['id']})] {repr(e)}"
                logger.error(error)
                await _record_target_error(target, e)
        scrape_duration = datetime.now() - scrape_start_ts
        logger.info("Scrape completed in %.1f seconds", scrape_duration.total_seconds())
    else:
        logger.info("Scraper is halted, skipping run")


async def broadcast(homes: list[Home]) -> None:
    subs = set()
    apns_client = apns.APNsClient()
    apns_invalid_counts: dict[int, int] = {}
    
    if db.get_dev_mode():
        subs = db.fetch_all(
            "SELECT * FROM hermes.subscribers WHERE (telegram_enabled = true OR apns_token IS NOT NULL) AND user_level > 1"
        )
    else:
        subs = db.fetch_all("SELECT * FROM hermes.subscribers WHERE telegram_enabled = true OR apns_token IS NOT NULL")
        
    # Create dict of agencies and their pretty names
    agencies = db.fetch_all("SELECT agency, user_info FROM hermes.targets")
    agencies = dict([(a["agency"], a["user_info"]["agency"]) for a in agencies])
    
    for home in homes:
        for sub in subs:
            # Apply filters
            sqm_ok = (sub["filter_min_sqm"] == 0) or (home.sqm == -1) or (home.sqm >= sub["filter_min_sqm"])
            if (home.price >= sub["filter_min_price"] and home.price <= sub["filter_max_price"]) and \
               (home.city.lower() in sub["filter_cities"]) and \
               (home.agency in sub["filter_agencies"]) and \
               sqm_ok:

                message = f"{meta.HOUSE_EMOJI} {home.address}, {home.city}\n"
                message += f"{meta.EURO_EMOJI} €{home.price}/m\n"
                if home.sqm > 0:
                    message += f"{meta.SQM_EMOJI} {home.sqm} m\u00b2\n"
                message += "\n"
                message = meta.escape_markdownv2(message)
                agency_name = agencies[home.agency]
                message += f"{meta.LINK_EMOJI} [{agency_name}]({home.url})"

                if sub.get("telegram_enabled") and sub.get("telegram_id"):
                    try:
                        await meta.BOT.send_message(text=message, chat_id=sub["telegram_id"], parse_mode="MarkdownV2")
                    except Forbidden as e:
                        # This means the user deleted their account or blocked the bot, so disable them
                        db.disable_user(sub["telegram_id"])
                        logger.info("Removed subscriber telegram_id=%s due to broadcast failure: %r", sub['telegram_id'], e)
                    except Exception as e:
                        # Log any other exceptions
                        logger.warning("Failed to broadcast to telegram_id=%s: %r", sub['telegram_id'], e)

                apns_token = sub.get("apns_token")
                if not apns_token or not apns_client.enabled:
                    continue

                payload = apns.build_home_notification_payload(home, agency_name)
                result = None
                for attempt in range(1, APNS_MAX_RETRIES + 1):
                    result = apns_client.send(apns_token, payload)
                    if result.ok:
                        _increment_scraper_metric("apns", "success")
                        logger.debug(
                            "APNs send success subscriber_id=%s device_id=%s",
                            sub.get("id"),
                            sub.get("device_id"),
                        )
                        break
                    if not result.should_retry or attempt == APNS_MAX_RETRIES:
                        break
                    backoff_seconds = APNS_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                    sleep(backoff_seconds)

                if result is None or result.ok:
                    continue

                logger.warning(
                    "APNs send failure subscriber_id=%s device_id=%s status=%s reason=%s retryable=%s",
                    sub.get("id"),
                    sub.get("device_id"),
                    result.status_code,
                    result.reason,
                    result.should_retry,
                )
                _increment_scraper_metric("apns", "failure")

                if result.permanent_invalid and sub.get("id") is not None:
                    sub_id = int(sub["id"])
                    apns_invalid_counts[sub_id] = apns_invalid_counts.get(sub_id, 0) + 1
                    if apns_invalid_counts[sub_id] >= APNS_INVALID_TOKEN_THRESHOLD:
                        db.clear_apns_token(sub_id)
                        logger.info(
                            "Cleared APNs token for subscriber_id=%s after %s invalid token failures",
                            sub_id,
                            apns_invalid_counts[sub_id],
                        )


async def scrape_site(target: dict) -> None:
    if target["method"] == "GET":
        r = requests.get(target["queryurl"], headers=target["headers"])
    elif target["method"] == "CF_GET":
        r = cf_requests.get(target["queryurl"], headers=target["headers"], impersonate="chrome124")
    elif target["method"] == "POST":
        r = requests.post(target["queryurl"], json=target["post_data"], headers=target["headers"])
    elif target["method"] == "POST_NDJSON":
        post_data = "\n".join(json.dumps(obj, separators=(",", ":")) for obj in target["post_data"]) + "\n"
        r = requests.post(target["queryurl"], data=post_data, headers=target["headers"])
    else:
        raise ValueError(f"Unknown method {target['method']} for target id {target['id']}")
        
    if r.status_code == 200:
        prev_homes: list[Home] = []
        new_homes: list[Home] = []
        
        for home in db.fetch_all("SELECT address, city FROM hermes.homes WHERE date_added > now() - interval '180 day'"):
            prev_homes.append(Home(home["address"], home["city"]))
        for home in HomeResults(target["agency"], r):
            if home not in prev_homes:
                new_homes.append(home)

        logger.info("[%s] Found %d new homes (of %d total parsed)", target["agency"], len(new_homes), len(new_homes) + len(prev_homes))
        for home in new_homes:
            logger.debug("[%s] New: %s, %s - €%d", target["agency"], home.address, home.city, home.price)
            db.add_home(home.url,
                        home.address,
                        home.city,
                        home.price,
                        home.agency,
                        datetime.now().isoformat(),
                        home.sqm)

        await broadcast(new_homes)

        try:
            enqueue_for_enrichment(new_homes)
        except Exception as e:
            logger.error("Enrichment enqueue failed: %r", e)
    else:
        raise ConnectionError(f"Got a non-OK status code: {r.status_code}")
    

if __name__ == '__main__':
    setup_logging()
    run(main())
