import sys
import asyncio
import argparse

import hermes_utils.db as db
import hermes_utils.meta as meta
import hermes_utils.strings as strings


async def _send(telegram_id: str, text: str) -> bool:
    try:
        await meta.BOT.send_message(chat_id=telegram_id, text=text)
        return True
    except Exception as e:
        print(f"  Telegram message failed for {telegram_id}: {e}")
        return False


def list_users():
    users = db.fetch_all(
        "SELECT telegram_id, user_level, daily_analysis_limit, date_added, telegram_enabled "
        "FROM hermes.subscribers ORDER BY date_added"
    )
    if not users:
        print("No subscribers.")
        return

    print(f"\n{'Telegram ID':<16} {'Level':<7} {'AI limit':<10} {'Active':<8} {'Joined'}")
    print("-" * 70)
    for u in users:
        limit = "unlimited" if u["daily_analysis_limit"] == -1 else str(u["daily_analysis_limit"])
        active = "yes" if u["telegram_enabled"] else "no"
        print(f"{u['telegram_id']:<16} {u['user_level']:<7} {limit:<10} {active:<8} {u['date_added']}")
    print(f"\n{len(users)} subscriber(s).\n")


def promote(telegram_id: str):
    sub = db.fetch_one(
        "SELECT telegram_id, daily_analysis_limit FROM hermes.subscribers WHERE telegram_id = %s",
        [telegram_id],
    )
    if not sub:
        print(f"No subscriber found with Telegram ID {telegram_id}.")
        return
    if sub["daily_analysis_limit"] == -1:
        print(f"User {telegram_id} already has unlimited analyses.")
        return

    db.promote_user(int(telegram_id))
    print(f"Promoted user {telegram_id} to unlimited AI analyses.")

    message = strings.get("promoted_notification", int(telegram_id))
    sent = asyncio.run(_send(telegram_id, message))
    if sent:
        print("  Notification sent via Telegram.")


def ban(telegram_id: str):
    sub = db.fetch_one(
        "SELECT telegram_id FROM hermes.subscribers WHERE telegram_id = %s",
        [telegram_id],
    )
    if not sub:
        print(f"No subscriber found with Telegram ID {telegram_id}.")
        return

    message = strings.get("banned_notification", int(telegram_id))
    sent = asyncio.run(_send(telegram_id, message))
    if sent:
        print("  Notification sent via Telegram.")

    db.deny_user(int(telegram_id))
    print(f"Removed user {telegram_id}.")


def main():
    parser = argparse.ArgumentParser(
        prog="hermes-cli",
        description="Manage Hermes subscribers"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all subscribers")
    promote_cmd = sub.add_parser("promote", help="Grant unlimited AI analyses to a user")
    promote_cmd.add_argument("telegram_id", help="Telegram ID to promote")
    ban_cmd = sub.add_parser("ban", help="Remove a user")
    ban_cmd.add_argument("telegram_id", help="Telegram ID to ban")

    args = parser.parse_args()

    if args.command == "promote":
        promote(args.telegram_id)
    elif args.command == "ban":
        ban(args.telegram_id)
    else:
        list_users()


if __name__ == "__main__":
    main()
