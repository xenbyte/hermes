import re
import logging
import functools
import telegram
from time import sleep
from telegram.error import Forbidden
from telegram.ext import filters, MessageHandler, ApplicationBuilder, CommandHandler, CallbackQueryHandler, ConversationHandler, ContextTypes

import hermes_utils.db as db
import hermes_utils.meta as meta
import hermes_utils.secrets as secrets
import hermes_utils.strings as strings
from hermes_utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


def initialize():
    logger.info("Initializing application...")

    if db.get_scraper_halted():
        logger.warning("Scraper is halted")
        
    if db.get_dev_mode():
        logger.warning("Dev mode is enabled")


def privileged(chat: telegram.Chat, msg: str, command: str, check_only: bool = True) -> bool:
    admins = db.fetch_all("SELECT * FROM hermes.subscribers WHERE user_level = 9")
    admin_chat_ids = [int(admin["telegram_id"]) for admin in admins]
    
    if chat and chat.id in admin_chat_ids:
        if not check_only:
            logger.info("Admin command /%s by chat_id=%s: %s", command, chat.id, msg)
        return True
    else:
        if not check_only:
            logger.warning("Unauthorized /%s attempted by chat_id=%s", command, chat.id)
        return False


def parse_argument(text: str, key: str) -> dict:
    arg = re.search(rf"{key}=(.*?)(?:\s|$)", text)
    
    if not arg:
        return dict()
    
    start, end = arg.span()
    stripped_text = text[:start] + text[end:]
    
    value = arg.group(1)
    
    return {"text": stripped_text, "key": key, "value": value}


async def get_sub_name(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    if not update.effective_chat: return ""
    if update.effective_chat.username: return update.effective_chat.username
    return str((await context.bot.get_chat(update.effective_chat.id)).first_name)


def requires_approval(func):
    @functools.wraps(func)
    async def wrapper(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_chat:
            return
        if not db.is_user_approved(update.effective_chat.id):
            await context.bot.send_message(
                update.effective_chat.id,
                strings.get("pending_approval", update.effective_chat.id)
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


async def start(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat: return
    logger.debug("/start from chat_id=%s", update.effective_chat.id)
    await context.bot.send_message(update.effective_chat.id, strings.get("welcome", update.effective_chat.id), disable_web_page_preview=True)

    # Handle deep-link payload for web account linking (Telegram always uses /start for these)
    payload = context.args[0] if context.args else None
    if payload and payload.startswith("hermes-web-link-"):
        checksub = db.fetch_one("SELECT * FROM hermes.subscribers WHERE telegram_id = %s", [str(update.effective_chat.id)])
        if checksub and checksub.get("approved"):
            await link(update, context, payload[16:])


async def register(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat: return
    logger.debug("/register from chat_id=%s", update.effective_chat.id)
    checksub = db.fetch_one("SELECT * FROM hermes.subscribers WHERE telegram_id = %s", [str(update.effective_chat.id)])

    if not checksub:
        name = await get_sub_name(update, context)
        logger.info("New subscriber: %s (chat_id=%s)", name, update.effective_chat.id)
        db.add_user(update.effective_chat.id)
        await context.bot.send_message(update.effective_chat.id, strings.get("pending_approval", update.effective_chat.id))
    elif not checksub.get("approved"):
        await context.bot.send_message(update.effective_chat.id, strings.get("register_pending", update.effective_chat.id))
    elif checksub["telegram_enabled"]:
        await context.bot.send_message(update.effective_chat.id, strings.get("register_already", update.effective_chat.id))
    else:
        # Approved but previously stopped — re-enable
        name = await get_sub_name(update, context)
        logger.info("Re-enabled subscriber: %s (chat_id=%s)", name, update.effective_chat.id)
        db.enable_user(update.effective_chat.id)
        await context.bot.send_message(update.effective_chat.id, strings.get("start", update.effective_chat.id), parse_mode="MarkdownV2")


async def info(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat: return
    await context.bot.send_message(update.effective_chat.id, strings.get("info", update.effective_chat.id), disable_web_page_preview=True)


@requires_approval
async def stop(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat: return
    checksub = db.fetch_one("SELECT * FROM hermes.subscribers WHERE telegram_id = %s", [str(update.effective_chat.id)])

    if checksub:
        if checksub["telegram_enabled"]:
            # Disabling is setting telegram_enabled to false in the db
            db.disable_user(update.effective_chat.id)
            
            name = await get_sub_name(update, context)
            logger.info("Removed subscriber: %s (chat_id=%s)", name, update.effective_chat.id)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=strings.get("stop", update.effective_chat.id, [db.get_donation_link()]),
        parse_mode="MarkdownV2",
        disable_web_page_preview=True
    )


async def announce(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return
    if not privileged(update.effective_chat, update.message.text, "announce", check_only=False): return
        
    if db.get_dev_mode():
        subs = db.fetch_all("SELECT * FROM hermes.subscribers WHERE subscription_expiry IS NOT NULL AND telegram_enabled = true AND user_level > 1")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Dev mode is enabled, message not broadcasted to all subscribers")
    else:
        subs = db.fetch_all("SELECT * FROM hermes.subscribers WHERE subscription_expiry IS NOT NULL AND telegram_enabled = true")

    # Remove /announce
    msg = update.message.text[10:]
    
    # Parse arguments
    markdown = parse_argument(msg, "Markdown")
    if markdown:
        msg = markdown['text']
    else:
        markdown['value'] = False
        
    disablepreview = parse_argument(msg, "DisableLinkPreview")
    if disablepreview:
        msg = disablepreview['text']
    else:
        disablepreview['value'] = False

    for sub in subs:
        sleep(1/29)  # avoid rate limit (broadcasting to max 30 users per second)
        try:
            if markdown['value']:
                await context.bot.send_message(sub["telegram_id"], msg, parse_mode="MarkdownV2", disable_web_page_preview=bool(disablepreview['value']))
            else:
                await context.bot.send_message(sub["telegram_id"], msg, disable_web_page_preview=bool(disablepreview['value']))
        except Forbidden as e:
            # This means the user deleted their account or blocked the bot, so disable them
            db.disable_user(sub["telegram_id"])
            logger.info("Removed subscriber telegram_id=%s due to announce failure: %r", sub['telegram_id'], e)
        except BaseException as e:
            logger.warning("Exception while broadcasting announcement to telegram_id=%s: %r", sub['telegram_id'], e)
            continue


@requires_approval
async def websites(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat: return
    targets = db.fetch_all("SELECT agency, user_info FROM hermes.targets WHERE enabled = true")

    message = strings.get("websites", update.effective_chat.id)
    
    # Some agencies have multiple targets, but that's duplicate information for the user
    already_included = []
    
    for target in targets:
        if target["agency"] in already_included:
            continue
            
        already_included.append(target["agency"])
        message += strings.get("website_info", update.effective_chat.id, [target['user_info']['agency'], target['user_info']['website']])
        
    await context.bot.send_message(update.effective_chat.id, message[:-1])
    await context.bot.send_message(update.effective_chat.id, strings.get("source_code", update.effective_chat.id))


async def halt(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return
    if not privileged(update.effective_chat, update.message.text, "halt", check_only=False): return
    db.halt_scraper()
    await context.bot.send_message(update.effective_chat.id, "Scraper halted")


async def resume(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return
    if not privileged(update.effective_chat, update.message.text, "resume", check_only=False): return
    
    if db.get_scraper_halted():
        db.resume_scraper()
        await context.bot.send_message(update.effective_chat.id, "Resuming scraper. Note that this may create a massive update within the next 5 minutes. Consider enabling /dev mode.")
    else:
        await context.bot.send_message(update.effective_chat.id, "Scraper is not halted")


async def enable_dev(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return
    if not privileged(update.effective_chat, update.message.text, "dev", check_only=False): return
    db.enable_dev_mode()
    await context.bot.send_message(update.effective_chat.id, "Dev mode enabled")


async def disable_dev(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return
    if not privileged(update.effective_chat, update.message.text, "nodev", check_only=False): return
    db.disable_dev_mode()
    await context.bot.send_message(update.effective_chat.id, "Dev mode disabled")


async def status(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return
    if not privileged(update.effective_chat, update.message.text, "status", check_only=False): return
    
    message = f"Running version: {meta.APP_VERSION}\n\n"
    
    if db.get_dev_mode():
        message += f"{meta.CROSS_EMOJI} Dev mode: enabled\n"
    else:
        message += f"{meta.CHECK_EMOJI} Dev mode: disabled\n"
        
    if db.get_scraper_halted():
        message += f"{meta.CROSS_EMOJI} Scraper: halted\n"
    else:
        message += f"{meta.CHECK_EMOJI} Scraper: active\n"

    active_sub_count = db.fetch_one("SELECT COUNT(*) FROM hermes.subscribers WHERE telegram_enabled = true")
    sub_count = db.fetch_one("SELECT COUNT(*) FROM hermes.subscribers")
    message += "\n"
    message += f"Active subscriber count: {active_sub_count['count']}\n"
    message += f"Total subscriber count: {sub_count['count']}\n"
    
    donation_link = db.fetch_one("SELECT donation_link, donation_link_updated FROM hermes.meta")
    message += "\n"
    message += f"Current donation link: {donation_link['donation_link']}\n"
    message += f"Last updated: {donation_link['donation_link_updated']}\n"

    targets = db.fetch_all("SELECT * FROM hermes.targets")
    message += "\n"
    message += "Targets (id): listings in past 7 days\n"
        
    for target in targets:
        agency = target["agency"]
        target_id = target["id"]
        count = db.fetch_one("SELECT COUNT(*) FROM hermes.homes WHERE agency = %s AND date_added > now() - '1 week'::interval", [agency])
        message += f"{agency} ({target_id}): {count['count']} listings\n"

    await context.bot.send_message(update.effective_chat.id, message, disable_web_page_preview=True)


async def set_donation_link(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return
    if not privileged(update.effective_chat, update.message.text, "setdonationlink", check_only=False): return
    
    db.update_donation_link(update.message.text.split(' ')[1])
    await context.bot.send_message(update.effective_chat.id, "Donation link updated")
    

@requires_approval
async def filter(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return
    logger.debug("/filter from chat_id=%s: %s", update.effective_chat.id, update.message.text)
    try:
        cmd = [token.lower() for token in update.message.text.split(' ')]
    except AttributeError:
        # This means the user edited a message, do nothing
        return
    
    # Fetch subscriber from database
    sub = db.fetch_one("SELECT * FROM hermes.subscribers WHERE telegram_id = %s", [str(update.effective_chat.id)])
    if not sub:
        logger.error("Subscriber chat_id=%s used /filter but is not in database. Msg: %s", update.effective_chat.id, update.message.text)
        await context.bot.send_message(update.effective_chat.id, "Couldn't fetch your filter settings; this is unexpected, please report it")
        return
    
    # '/filter' only
    if len(cmd) == 1:

        cities_str = ""
        for c in sub["filter_cities"]:
            cities_str += f"{c.title()}, "

        message = strings.get("filter", update.effective_chat.id, [sub['filter_min_price'], sub['filter_max_price'], sub['filter_min_sqm'], cities_str[:-2]])
        
    # Set minprice filter
    elif len(cmd) == 3 and cmd[1] in ["minprice", "min"]:
        try:
            minprice = int(cmd[2])
        except ValueError:
            await context.bot.send_message(update.effective_chat.id, strings.get("filter_invalid_number", update.effective_chat.id, [cmd[2]]))
            return
            
        db.set_filter_minprice(update.effective_chat, minprice)
        message = strings.get("filter_minprice", update.effective_chat.id, [str(minprice)])
    
    # Set maxprice filter
    elif len(cmd) == 3 and cmd[1] in ["maxprice", "max"]:
        try:
            maxprice = int(cmd[2])
        except ValueError:
            await context.bot.send_message(update.effective_chat.id, strings.get("filter_invalid_number", update.effective_chat.id, [cmd[2]]))
            return

        db.set_filter_maxprice(update.effective_chat, maxprice)
        message = strings.get("filter_maxprice", update.effective_chat.id, [str(maxprice)])

    # Set minsqm filter
    elif len(cmd) == 3 and cmd[1] in ["minsqm", "sqm"]:
        try:
            minsqm = int(cmd[2])
        except ValueError:
            await context.bot.send_message(update.effective_chat.id, strings.get("filter_invalid_number", update.effective_chat.id, [cmd[2]]))
            return

        db.set_filter_minsqm(update.effective_chat, minsqm)
        message = strings.get("filter_minsqm", update.effective_chat.id, [str(minsqm)])

    # View city possibilities
    elif len(cmd) == 2 and cmd[1] == "city":
        all_filter_cities = [c["city"] for c in db.fetch_all("SELECT DISTINCT city FROM hermes.homes")]
        all_filter_cities.sort()
        
        message = strings.get("filter_city_header", update.effective_chat.id)
        for city in all_filter_cities:
            message += city.title() + "\n"
            if len(message) > 4000:
                await context.bot.send_message(update.effective_chat.id, message, parse_mode="Markdown")
                message = ""
            
        message += strings.get("filter_city_trailer", update.effective_chat.id)

    # Modify agency filter
    elif len(cmd) == 2 and cmd[1] in ["agency", "agencies", "website", "websites"]:
        included, reply_keyboard = [], []
        enabled_agencies = db.fetch_one("SELECT filter_agencies FROM hermes.subscribers WHERE telegram_id = %s", [str(update.effective_chat.id)])["filter_agencies"]
        for row in db.fetch_all("SELECT agency, user_info FROM hermes.targets WHERE enabled = true"):
            if row["agency"] not in included:
                included.append(row["agency"])
                if row["agency"] in enabled_agencies:
                    reply_keyboard.append([telegram.InlineKeyboardButton(meta.CHECK_EMOJI + " " + row["user_info"]["agency"], callback_data=f"hfa.d.{row['agency']}")])
                else:
                    reply_keyboard.append([telegram.InlineKeyboardButton(meta.CROSS_EMOJI + " " + row["user_info"]["agency"], callback_data=f"hfa.e.{row['agency']}")])

        await update.message.reply_text(strings.get("filter_agency", update.effective_chat.id), reply_markup=telegram.InlineKeyboardMarkup(reply_keyboard))
        return
            
    # Modify city filter
    elif len(cmd) >= 4 and cmd[1] == "city" and cmd[2] in ["add", "remove", "rm", "delete", "del"]:
        city = ""
        for token in cmd[3:]:
            # SQL injection is not possible here but you can call me paranoid that's absolutely fine
            city += token.replace(';', '').replace('"', '').replace("'", '') + ' '
        city = city[:-1]
        
        # Get cities currently in filter of subscriber
        sub_filter_cities = db.fetch_one("SELECT filter_cities FROM hermes.subscribers WHERE telegram_id = %s", [str(update.effective_chat.id)])["filter_cities"]
        
        if cmd[2] == "add":
            # Get possible cities from database
            all_filter_cities = [c["city"] for c in db.fetch_all("SELECT DISTINCT city FROM hermes.homes")]
            all_filter_cities.sort()
            
            # Check if the city is valid
            if city not in [c.lower() for c in all_filter_cities]:
                message = strings.get("filter_city_invalid", update.effective_chat.id, [city.title()])
                await context.bot.send_message(update.effective_chat.id, message, parse_mode="Markdown")
                return
                
            if city not in sub_filter_cities:
                sub_filter_cities.append(city)
            else:
                message = strings.get("filter_city_already_in", update.effective_chat.id, [city.title()])
                await context.bot.send_message(update.effective_chat.id, message)
                return

            db.set_filter_cities(update.effective_chat, sub_filter_cities)
            message = strings.get("filter_city_added", update.effective_chat.id, [city.title()])
        else:
            if city in sub_filter_cities:
                sub_filter_cities.remove(city)
            else:
                message = strings.get("filter_city_not_in", update.effective_chat.id, [city.title()])
                await context.bot.send_message(update.effective_chat.id, message)
                return

            db.set_filter_cities(update.effective_chat, sub_filter_cities)
            message = strings.get("filter_city_removed", update.effective_chat.id, [city.title()])

            if len(sub_filter_cities) == 0:
                message += strings.get("filter_city_empty", update.effective_chat.id)
    else:
        message = strings.get("filter_invalid_command", update.effective_chat.id)
        
    await context.bot.send_message(update.effective_chat.id, message, parse_mode="Markdown")


@requires_approval
async def donate(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat: return
    donation_link = db.get_donation_link()

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=strings.get("donate", update.effective_chat.id, [donation_link]),
        parse_mode="MarkdownV2",
        disable_web_page_preview=True
    )


@requires_approval
async def faq(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat: return
    donation_link = db.get_donation_link()
    await context.bot.send_message(update.effective_chat.id, strings.get("faq", update.effective_chat.id, [donation_link]), parse_mode="MarkdownV2", disable_web_page_preview=True)


@requires_approval
async def set_lang_nl(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat: return
    db.set_user_lang(update.effective_chat, "nl")
    await context.bot.send_message(update.effective_chat.id, "Ik spreek vanaf nu Nederlands")


@requires_approval
async def set_lang_en(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat: return
    db.set_user_lang(update.effective_chat, "en")
    await context.bot.send_message(update.effective_chat.id, "I'll speak English")


async def callback_query_handler(update: telegram.Update, _) -> None:
    query = update.callback_query
    if not query or not query.data or not query.message: return

    # Existing agency filter callbacks (dot-separated)
    if query.data.startswith("hfa."):
        cbid, action, agency = query.data.split(".")
        included, reply_keyboard = [], []

        enabled_agencies: set[str] = set(db.fetch_one("SELECT filter_agencies FROM hermes.subscribers WHERE telegram_id = %s", [str(query.message.chat.id)])["filter_agencies"])
        if action == "d":
            try:
                enabled_agencies.remove(agency)
            except KeyError:
                pass
        elif action == "e":
            enabled_agencies.add(agency)
        db.set_filter_agencies(query.message.chat, enabled_agencies)

        for row in db.fetch_all("SELECT agency, user_info FROM hermes.targets WHERE enabled = true"):
            if row["agency"] not in included:
                included.append(row["agency"])
                if row["agency"] in enabled_agencies:
                    reply_keyboard.append([telegram.InlineKeyboardButton(meta.CHECK_EMOJI + " " + row["user_info"]["agency"], callback_data=f"hfa.d.{row['agency']}")])
                else:
                    reply_keyboard.append([telegram.InlineKeyboardButton(meta.CROSS_EMOJI + " " + row["user_info"]["agency"], callback_data=f"hfa.e.{row['agency']}")])
        await query.answer()
        await query.edit_message_reply_markup(telegram.InlineKeyboardMarkup(reply_keyboard))

    # Letter generation callbacks (colon-separated)
    elif query.data.startswith("letter_"):
        parts = query.data.split(":", 1)
        if len(parts) != 2:
            return
        action, callback_id = parts
        language = "nl" if action == "letter_nl" else "en"

        await query.answer("Generating letter, please wait...")

        try:
            from enrichment.profile import get_profile_for_telegram_id
            from enrichment.letters import generate_letter

            profile = get_profile_for_telegram_id(str(query.message.chat.id))
            if not profile:
                await query.message.reply_text("No profile found. Use /profile edit to create one.")
                return

            verdict = db.fetch_one(
                "SELECT * FROM hermes.enrichment_results "
                "WHERE id LIKE %s AND profile_id = %s",
                [callback_id + "%", profile["id"]],
            )
            if not verdict:
                await query.message.reply_text("Listing analysis not found.")
                return

            letter = generate_letter(profile, dict(verdict), language)
            await query.message.reply_text(letter)
        except Exception as e:
            logger.error("Letter generation callback failed: %r", e)
            await query.message.reply_text("Something went wrong generating the letter. Please try again.")


# ─── Profile wizard ──────────────────────────────────────────────────────────
# States for ConversationHandler
(P_NAME, P_NATIONALITY, P_EMPLOYER, P_CONTRACT,
 P_INCOME, P_MAX_RENT, P_CITIES, P_OCCUPANTS,
 P_PETS, P_MOVE_IN, P_NOTES) = range(11)


async def _profile_cancel(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat: return ConversationHandler.END
    context.user_data.pop("profile", None)
    await context.bot.send_message(update.effective_chat.id, "Profile setup cancelled.")
    return ConversationHandler.END


# ── Step helpers ──────────────────────────────────────────────────────────────

async def _ask_nationality(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(
        update.effective_chat.id,
        "What's your nationality? (e.g. Dutch, British)\n/skip to skip"
    )
    return P_NATIONALITY


async def _ask_employer(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(
        update.effective_chat.id,
        "Where do you work? (employer name)\n/skip to skip"
    )
    return P_EMPLOYER


async def _ask_contract(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [telegram.InlineKeyboardButton("Permanent", callback_data="pwiz_contract:permanent"),
         telegram.InlineKeyboardButton("Temporary", callback_data="pwiz_contract:temporary")],
        [telegram.InlineKeyboardButton("Freelance / ZZP", callback_data="pwiz_contract:freelance"),
         telegram.InlineKeyboardButton("Student", callback_data="pwiz_contract:student")],
        [telegram.InlineKeyboardButton("Skip", callback_data="pwiz_contract:skip")],
    ]
    await context.bot.send_message(
        update.effective_chat.id,
        "What type of employment contract do you have?",
        reply_markup=telegram.InlineKeyboardMarkup(keyboard),
    )
    return P_CONTRACT


async def _ask_income(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(
        update.effective_chat.id,
        "What's your gross monthly income? (EUR, e.g. 4500)\n/skip to skip"
    )
    return P_INCOME


async def _ask_max_rent(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(
        update.effective_chat.id,
        "What's your maximum rent? (EUR/month, e.g. 1800)"
    )
    return P_MAX_RENT


async def _ask_cities(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    existing = db.fetch_one(
        "SELECT filter_cities FROM hermes.subscribers WHERE telegram_id = %s",
        [str(update.effective_chat.id)]
    )
    hint = ""
    if existing and existing.get("filter_cities"):
        hint = f"\n\nYour current city filter: {', '.join(c.title() for c in existing['filter_cities'])}"
    await context.bot.send_message(
        update.effective_chat.id,
        f"Which cities are you looking in? (comma-separated, e.g. Amsterdam, Almere){hint}"
    )
    return P_CITIES


async def _ask_occupants(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [telegram.InlineKeyboardButton("Just me", callback_data="pwiz_occupants:single"),
         telegram.InlineKeyboardButton("Couple", callback_data="pwiz_occupants:couple")],
        [telegram.InlineKeyboardButton("With roommates", callback_data="pwiz_occupants:roommates"),
         telegram.InlineKeyboardButton("Family", callback_data="pwiz_occupants:family")],
    ]
    await context.bot.send_message(
        update.effective_chat.id,
        "Who will live there?",
        reply_markup=telegram.InlineKeyboardMarkup(keyboard),
    )
    return P_OCCUPANTS


async def _ask_pets(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(
        update.effective_chat.id,
        "Do you have any pets?\n/skip to skip"
    )
    return P_PETS


async def _ask_move_in(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(
        update.effective_chat.id,
        "When do you want to move in? (e.g. May 2026, ASAP)\n/skip to skip"
    )
    return P_MOVE_IN


async def _ask_notes(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(
        update.effective_chat.id,
        "Anything else the AI should know? (e.g. quiet professional, references available)\n/skip to finish"
    )
    return P_NOTES


async def _profile_save(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from enrichment.profile import upsert_profile
    chat_id = update.effective_chat.id
    data = context.user_data.pop("profile", {})
    try:
        upsert_profile(str(chat_id), data)
        name = data.get("full_name", "?")
        max_rent = data.get("max_rent", "?")
        cities = ", ".join(c.title() for c in (data.get("target_cities") or []))
        employer = data.get("employer", "—")
        await context.bot.send_message(
            chat_id,
            f"✅ Profile saved!\n\n"
            f"Name: {name}\n"
            f"Employer: {employer}\n"
            f"Max rent: €{max_rent}/month\n"
            f"Cities: {cities}\n\n"
            "The AI will use this to score listings and write motivation letters.\n"
            "Use /profile to review, or /profile edit <field> <value> to change individual fields."
        )
    except Exception as e:
        logger.error("Profile save failed: %r", e)
        await context.bot.send_message(chat_id, "Something went wrong saving your profile. Please try again with /profile setup.")
    return ConversationHandler.END


# ── Step handlers ─────────────────────────────────────────────────────────────

async def _p_name(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text: return P_NAME
    context.user_data["profile"]["full_name"] = update.message.text.strip()
    return await _ask_nationality(update, context)


async def _p_nationality(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text: return P_NATIONALITY
    context.user_data["profile"]["nationality"] = update.message.text.strip()
    return await _ask_employer(update, context)

async def _p_skip_nationality(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat: return ConversationHandler.END
    return await _ask_employer(update, context)


async def _p_employer(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text: return P_EMPLOYER
    context.user_data["profile"]["employer"] = update.message.text.strip()
    return await _ask_contract(update, context)

async def _p_skip_employer(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat: return ConversationHandler.END
    return await _ask_contract(update, context)


async def _p_contract(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not update.effective_chat: return P_CONTRACT
    await query.answer()
    value = query.data.split(":")[1]
    if value != "skip":
        context.user_data["profile"]["contract_type"] = value
    await query.edit_message_reply_markup(None)
    return await _ask_income(update, context)


async def _p_income(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text: return P_INCOME
    try:
        context.user_data["profile"]["gross_monthly_income"] = int(update.message.text.strip().replace(",", "").replace(".", ""))
    except ValueError:
        await context.bot.send_message(update.effective_chat.id, "Please enter a number (e.g. 4500)\n/skip to skip")
        return P_INCOME
    return await _ask_max_rent(update, context)

async def _p_skip_income(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat: return ConversationHandler.END
    return await _ask_max_rent(update, context)


async def _p_max_rent(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text: return P_MAX_RENT
    try:
        context.user_data["profile"]["max_rent"] = int(update.message.text.strip().replace(",", "").replace(".", ""))
    except ValueError:
        await context.bot.send_message(update.effective_chat.id, "Please enter a number (e.g. 1800)")
        return P_MAX_RENT
    return await _ask_cities(update, context)


async def _p_cities(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text: return P_CITIES
    cities = [c.strip().lower() for c in update.message.text.split(",") if c.strip()]
    if not cities:
        await context.bot.send_message(update.effective_chat.id, "Please enter at least one city.")
        return P_CITIES
    context.user_data["profile"]["target_cities"] = cities
    return await _ask_occupants(update, context)


async def _p_occupants(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not update.effective_chat: return P_OCCUPANTS
    await query.answer()
    context.user_data["profile"]["occupants"] = query.data.split(":")[1]
    await query.edit_message_reply_markup(None)
    return await _ask_pets(update, context)


async def _p_pets(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text: return P_PETS
    context.user_data["profile"]["pets"] = update.message.text.strip()
    return await _ask_move_in(update, context)

async def _p_skip_pets(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat: return ConversationHandler.END
    return await _ask_move_in(update, context)


async def _p_move_in(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text: return P_MOVE_IN
    context.user_data["profile"]["move_in_date"] = update.message.text.strip()
    return await _ask_notes(update, context)

async def _p_skip_move_in(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat: return ConversationHandler.END
    return await _ask_notes(update, context)


async def _p_notes(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text: return P_NOTES
    context.user_data["profile"]["extra_notes"] = update.message.text.strip()
    return await _profile_save(update, context)

async def _p_skip_notes(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat: return ConversationHandler.END
    return await _profile_save(update, context)


# ── /profile command (entry point + view + edit) ──────────────────────────────

@requires_approval
async def profile_cmd(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.message.text:
        return ConversationHandler.END

    try:
        from enrichment.profile import get_profile_for_telegram_id, upsert_profile
    except ImportError:
        await context.bot.send_message(update.effective_chat.id, "Enrichment module not available.")
        return ConversationHandler.END

    parts = update.message.text.strip().split(maxsplit=2)

    # /profile setup → start wizard
    if len(parts) >= 2 and parts[1] == "setup":
        context.user_data["profile"] = {}
        await context.bot.send_message(
            update.effective_chat.id,
            "Let's set up your profile for AI-powered listing analysis.\n\n"
            "This helps score listings and write tailored motivation letters.\n"
            "Send /cancel at any time to stop.\n\n"
            "What's your full name?"
        )
        return P_NAME

    # /profile edit <field> <value>
    if len(parts) >= 2 and parts[1] == "edit":
        text = update.message.text.strip()
        tokens = text.split(maxsplit=3)
        if len(tokens) < 4:
            await context.bot.send_message(update.effective_chat.id, "Usage: /profile edit <field> <value>")
            return ConversationHandler.END
        field, value = tokens[2], tokens[3]
        allowed = {
            "full_name", "age", "nationality", "languages", "bsn_held", "gemeente",
            "employer", "contract_type", "gross_monthly_income", "employment_duration",
            "work_address", "max_rent", "target_cities", "furnishing_pref", "occupants",
            "pets", "owned_items", "move_in_date", "extra_notes",
        }
        if field not in allowed:
            await context.bot.send_message(
                update.effective_chat.id,
                f"Unknown field: {field}\nAllowed: {', '.join(sorted(allowed))}",
            )
            return ConversationHandler.END
        if field in ("age", "gross_monthly_income", "max_rent"):
            try:
                value = int(value)
            except ValueError:
                await context.bot.send_message(update.effective_chat.id, f"{field} must be a number")
                return ConversationHandler.END
        elif field == "target_cities":
            value = [c.strip().lower() for c in value.split(",")]
        elif field == "languages":
            value = [lang.strip() for lang in value.split(",")]
        elif field == "bsn_held":
            value = value.lower() in ("true", "yes", "1")
        upsert_profile(str(update.effective_chat.id), {field: value})
        await context.bot.send_message(update.effective_chat.id, f"✅ Updated {field}")
        return ConversationHandler.END

    # /profile → view
    profile = get_profile_for_telegram_id(str(update.effective_chat.id))
    if not profile:
        await context.bot.send_message(
            update.effective_chat.id,
            "No profile set up yet.\n\nUse /profile setup to get started — it takes about 2 minutes and lets the AI score listings and write motivation letters tailored to you."
        )
        return ConversationHandler.END

    labels = [
        ("full_name", "Name"), ("nationality", "Nationality"), ("employer", "Employer"),
        ("contract_type", "Contract"), ("gross_monthly_income", "Income (EUR/mo)"),
        ("max_rent", "Max rent"), ("target_cities", "Cities"), ("occupants", "Occupants"),
        ("pets", "Pets"), ("move_in_date", "Move-in"), ("extra_notes", "Notes"),
    ]
    msg = "Your profile:\n\n"
    for key, label in labels:
        val = profile.get(key)
        if val is not None:
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            msg += f"{label}: {val}\n"
    msg += "\nUse /profile setup to update, or /profile edit <field> <value> for a single field."
    await context.bot.send_message(update.effective_chat.id, msg)
    return ConversationHandler.END


@requires_approval
async def cost_cmd(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat: return

    try:
        from enrichment.costs import get_daily_spend, get_monthly_summary
    except ImportError:
        await context.bot.send_message(update.effective_chat.id, "Enrichment module not available.")
        return

    daily = get_daily_spend()
    monthly = get_monthly_summary()

    msg = f"LLM Costs\n\nToday: ${daily:.4f}\n"
    msg += f"This month: ${monthly.get('total_cost', 0):.4f}\n"
    msg += f"Total calls: {monthly.get('total_calls', 0)}\n"

    by_model = monthly.get("by_model")
    if by_model:
        msg += "\nBy model:\n"
        for model, info in by_model.items():
            cost = info["cost"] if isinstance(info, dict) else info
            msg += f"  {model}: ${cost:.4f}\n"

    await context.bot.send_message(update.effective_chat.id, msg)


@requires_approval
async def link(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE, code: str = "") -> None:
    if not update.effective_chat or not update.message or not update.message.text: return

    if not code:
        parts = update.message.text.split()
        if len(parts) < 2:
            await context.bot.send_message(update.effective_chat.id, strings.get("link_usage", update.effective_chat.id))
            return
        code = parts[1].strip().upper()

    result = db.link_account(update.effective_chat.id, code)

    if result == "success":
        await context.bot.send_message(update.effective_chat.id, strings.get("link_success", update.effective_chat.id))
    elif result == "already_linked":
        await context.bot.send_message(update.effective_chat.id, strings.get("link_already_linked", update.effective_chat.id))
    else:
        await context.bot.send_message(update.effective_chat.id, strings.get("link_invalid_code", update.effective_chat.id))


@requires_approval
async def help(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message or not update.message.text: return
    message = strings.get("help", update.effective_chat.id)
    
    if privileged(update.effective_chat, update.message.text, "help", check_only=True):
        message += "\n\n"
        message += "*Admin commands:*\n"
        message += "/announce - Broadcast a message to all subscribers\n"
        message += "/status - Get system status\n"
        message += "/halt - Halts the scraper\n"
        message += "/resume - Resumes the scraper\n"
        message += "/dev - Enables dev mode\n"
        message += "/nodev - Disables dev mode\n"
        message += "/setdonate - Sets the goodbye message donation link"

    await context.bot.send_message(update.effective_chat.id, message, parse_mode="Markdown")


if __name__ == '__main__':
    setup_logging()
    initialize()
    application = ApplicationBuilder().token(secrets.TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("info", info))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("announce", announce))
    application.add_handler(CommandHandler("websites", websites))
    application.add_handler(CommandHandler("donate", donate))
    application.add_handler(CommandHandler("filter", filter))
    application.add_handler(CommandHandler("filters", filter))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("halt", halt))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(CommandHandler("dev", enable_dev))
    application.add_handler(CommandHandler("nodev", disable_dev))
    application.add_handler(CommandHandler("setdonate", set_donation_link))
    application.add_handler(CommandHandler("link", link))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("profile", profile_cmd)],
        states={
            P_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, _p_name)],
            P_NATIONALITY:[MessageHandler(filters.TEXT & ~filters.COMMAND, _p_nationality),
                           CommandHandler("skip", _p_skip_nationality)],
            P_EMPLOYER:   [MessageHandler(filters.TEXT & ~filters.COMMAND, _p_employer),
                           CommandHandler("skip", _p_skip_employer)],
            P_CONTRACT:   [CallbackQueryHandler(_p_contract, pattern="^pwiz_contract:")],
            P_INCOME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, _p_income),
                           CommandHandler("skip", _p_skip_income)],
            P_MAX_RENT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, _p_max_rent)],
            P_CITIES:     [MessageHandler(filters.TEXT & ~filters.COMMAND, _p_cities)],
            P_OCCUPANTS:  [CallbackQueryHandler(_p_occupants, pattern="^pwiz_occupants:")],
            P_PETS:       [MessageHandler(filters.TEXT & ~filters.COMMAND, _p_pets),
                           CommandHandler("skip", _p_skip_pets)],
            P_MOVE_IN:    [MessageHandler(filters.TEXT & ~filters.COMMAND, _p_move_in),
                           CommandHandler("skip", _p_skip_move_in)],
            P_NOTES:      [MessageHandler(filters.TEXT & ~filters.COMMAND, _p_notes),
                           CommandHandler("skip", _p_skip_notes)],
        },
        fallbacks=[CommandHandler("cancel", _profile_cancel)],
        allow_reentry=True,
    ))
    application.add_handler(CommandHandler("cost", cost_cmd))
    application.add_handler(CommandHandler("help", help))
    application.add_handler(CommandHandler("faq", faq))
    application.add_handler(CommandHandler("nl", set_lang_nl))
    application.add_handler(CommandHandler("en", set_lang_en))
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), help))
    application.run_polling()
