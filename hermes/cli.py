import sys
import asyncio
import argparse

import hermes_utils.db as db
import hermes_utils.meta as meta
import hermes_utils.strings as strings


async def send_telegram_message(telegram_id: str, text: str) -> bool:
    try:
        await meta.BOT.send_message(chat_id=telegram_id, text=text)
        return True
    except Exception as e:
        print(f"  Failed to send Telegram message to {telegram_id}: {e}")
        return False


def list_pending():
    pending = db.get_pending_users()
    if not pending:
        print("No pending access requests.")
        return

    print(f"\n{'ID':<6} {'Telegram ID':<16} {'Requested at'}")
    print("-" * 50)
    for user in pending:
        print(f"{user['id']:<6} {user['telegram_id']:<16} {user['date_added']}")
    print(f"\n{len(pending)} pending request(s).\n")


def approve(telegram_id: str):
    sub = db.fetch_one(
        "SELECT id, telegram_id, approved FROM hermes.subscribers WHERE telegram_id = %s",
        [telegram_id]
    )
    if not sub:
        print(f"No subscriber found with Telegram ID {telegram_id}.")
        return
    if sub.get("approved"):
        print(f"User {telegram_id} is already approved.")
        return

    db.approve_user(int(telegram_id))
    print(f"Approved user {telegram_id}.")

    lang = db.get_user_lang(int(telegram_id))
    message = strings.get("approved_notification", int(telegram_id))
    sent = asyncio.run(send_telegram_message(telegram_id, message))
    if sent:
        print(f"  Notification sent via Telegram.")


def deny(telegram_id: str):
    sub = db.fetch_one(
        "SELECT id, telegram_id, approved FROM hermes.subscribers WHERE telegram_id = %s",
        [telegram_id]
    )
    if not sub:
        print(f"No subscriber found with Telegram ID {telegram_id}.")
        return
    if sub.get("approved"):
        print(f"User {telegram_id} is already approved — cannot deny.")
        return

    message = strings.get("denied_notification", int(telegram_id))
    sent = asyncio.run(send_telegram_message(telegram_id, message))
    if sent:
        print(f"  Notification sent via Telegram.")

    db.deny_user(int(telegram_id))
    print(f"Denied and removed user {telegram_id}.")


def main():
    parser = argparse.ArgumentParser(
        prog="hermes-cli",
        description="Manage Hermes access requests"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List pending access requests")
    approve_cmd = sub.add_parser("approve", help="Approve a user by Telegram ID")
    approve_cmd.add_argument("telegram_id", help="Telegram ID to approve")
    deny_cmd = sub.add_parser("deny", help="Deny a user by Telegram ID")
    deny_cmd.add_argument("telegram_id", help="Telegram ID to deny")

    args = parser.parse_args()

    if args.command == "approve":
        approve(args.telegram_id)
    elif args.command == "deny":
        deny(args.telegram_id)
    else:
        list_pending()


if __name__ == "__main__":
    main()
