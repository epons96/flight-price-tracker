"""Connects the tracker to your Telegram bot: asks for the bot token (hidden), finds your chat with
the bot, stores both as GitHub secrets with the gh CLI and sends a test message.

Before running it, create a bot with @BotFather (/newbot) and send your bot any message.
Needs only the standard library and gh logged in to GitHub."""

import getpass
import subprocess
import sys
from pathlib import Path

from telegram_api import TelegramError, call


def main():
    token = getpass.getpass("Bot token from @BotFather (it stays hidden as you paste it): ").strip()
    try:
        bot = call(token, "getMe")
    except TelegramError as e:
        sys.exit(f"That token doesn't work: {e}")
    print(f"Bot found: @{bot['username']}")

    while True:
        chats = [u["message"]["chat"] for u in call(token, "getUpdates")
                 if "message" in u and u["message"]["chat"]["type"] == "private"]
        if chats:
            break
        input(f"Send any message to @{bot['username']} in Telegram, then press Enter... ")
    chat = chats[-1]

    # gh reads the value from stdin, so it never shows up on screen or in the shell history.
    for name, value in (("TELEGRAM_TOKEN", token), ("TELEGRAM_CHAT_ID", str(chat["id"]))):
        subprocess.run(["gh", "secret", "set", name], input=value, text=True, check=True,
                       cwd=Path(__file__).parent)
    call(token, "sendMessage", {"chat_id": chat["id"],
                                "text": "Connected: the flight price digest will arrive here every morning."})
    print(f"Done: secrets saved and a test message sent to {chat.get('first_name', 'your chat')}.")


if __name__ == "__main__":
    main()
