import os
import re
import html
from typing import Optional

import requests
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SOURCE_CHANNEL = int(os.getenv("SOURCE_CHANNEL", "0"))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"


def validate_env():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set")
    if SOURCE_CHANNEL == 0:
        raise ValueError("SOURCE_CHANNEL is not set")
    if not DISCORD_WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL is not set")


def log(*args):
    if DEBUG:
        print(*args)


def remove_source_header(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned = []

    for i, line in enumerate(lines):
        if i == 0 and re.match(r"^\s*🚀\s*Новый твит от\s+.+", line, flags=re.IGNORECASE):
            continue
        cleaned.append(line)

    return "\n".join(cleaned).strip()


def remove_footer_twitter_link(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned = []

    for line in lines:
        if re.match(r"^\s*🔗\s+https?://(twitter\.com|x\.com)/\S+\s*$", line, flags=re.IGNORECASE):
            continue
        cleaned.append(line)

    return "\n".join(cleaned).strip()


def remove_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text)


def cleanup_whitespace(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r", "")
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def basic_cleanup(text: str) -> str:
    text = remove_source_header(text)
    text = remove_footer_twitter_link(text)
    text = remove_urls(text)
    text = cleanup_whitespace(text)
    return text


def transform_ifttt_text(raw_text: str) -> Optional[str]:
    text = basic_cleanup(raw_text)
    return text or None


def split_discord_message(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]

    parts = []
    current = ""

    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue

        candidate = f"{current}\n\n{block}".strip() if current else block

        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                parts.append(current)
            if len(block) <= limit:
                current = block
            else:
                for i in range(0, len(block), limit):
                    chunk = block[i:i + limit].strip()
                    if chunk:
                        parts.append(chunk)
                current = ""

    if current:
        parts.append(current)

    return parts


def send_to_discord(text: str) -> None:
    parts = split_discord_message(text)

    for part in parts:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": part},
            timeout=20,
        )

        if response.status_code not in (200, 204):
            raise RuntimeError(
                f"Discord webhook failed: {response.status_code} {response.text}"
            )


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.channel_post
    if not msg:
        return

    chat = msg.chat
    raw_text = msg.text or msg.caption or ""

    log("Incoming channel:", chat.id, "|", chat.title)
    log("Raw text:\n", raw_text)

    if str(chat.id) != str(SOURCE_CHANNEL):
        log("Skip: not source channel")
        return

    result = transform_ifttt_text(raw_text)
    if not result:
        log("Skip: empty result after transform")
        return

    log("Final text:\n", result)

    try:
        send_to_discord(result)
        log("Sent message to Discord")
    except Exception as e:
        log("Discord send error:", str(e))


def main():
    validate_env()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post)
    )

    print("TG → Discord bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
