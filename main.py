import os
import re
import html
import base64
import requests
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SOURCE_CHANNEL = int(os.getenv("SOURCE_CHANNEL", "0"))
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "0"))
TARGET_MESSAGE_THREAD_ID = int(os.getenv("TARGET_MESSAGE_THREAD_ID", "0"))
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

# Pipedream webhook URL
PIPEDREAM_WEBHOOK_URL = "https://eon5ixlgvwu4zqi.m.pipedream.net"

# =========================================================


def validate_env():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set")
    if SOURCE_CHANNEL == 0:
        raise ValueError("SOURCE_CHANNEL is not set")
    if TARGET_CHAT_ID == 0:
        raise ValueError("TARGET_CHAT_ID is not set")
    if not PIPEDREAM_WEBHOOK_URL:
        raise ValueError("PIPEDREAM_WEBHOOK_URL is not set")


def log(*args):
    if DEBUG:
        print(*args)


# =========================================================
# SHARED CLEANUP
# =========================================================

def remove_source_header(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned = []

    for i, line in enumerate(lines):
        # убрать "🚀 Новый твит от ..."
        if i == 0 and re.match(r"^\s*🚀\s*Новый твит от\s+.+", line, flags=re.IGNORECASE):
            continue

        # убрать "150$-750$ trading challenge" / "250$-1250$ trading challenge"
        if i == 0 and re.match(
            r"^\s*\d+(?:\.\d+)?\$?\s*-\s*\d+(?:\.\d+)?\$?\s+trading\s+challenge\s*$",
            line,
            flags=re.IGNORECASE,
        ):
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


def should_drop_line(line: str) -> bool:
    low = line.lower().strip()

    ad_patterns = [
        "you can copy my trades now",
        "copy my trades now",
        "steps & conditions to follow",
        "steps and conditions to follow",
        "signup & deposit",
        "sign up & deposit",
        "sign up and deposit",
        "signup and deposit",
        "copy my trades",
        "copy trade",
        "partner.",
        "blofin",
        "join my",
        "telegram link",
        "announce giveaway",
        "followers left",
        "free telegram link",
        "send my budd",
    ]

    mention_patterns = [
        "@crypto_zipsy",
        "@crypto_arki",
    ]

    if any(p in low for p in ad_patterns):
        return True

    if any(p in low for p in mention_patterns):
        return True

    if low.startswith("→ signup"):
        return True

    if low.startswith("→ copy"):
        return True

    return False


def remove_unwanted_lines(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned = []

    for line in lines:
        if should_drop_line(line):
            continue
        cleaned.append(line)

    return "\n".join(cleaned).strip()


def basic_cleanup(text: str) -> str:
    text = remove_source_header(text)
    text = remove_footer_twitter_link(text)
    text = remove_urls(text)
    text = remove_unwanted_lines(text)
    text = cleanup_whitespace(text)
    return text


# =========================================================
# TELEGRAM TRANSFORM
# =========================================================

def normalize_money_ranges(line: str) -> str:
    line = re.sub(r"(\d+(?:\.\d+)?\$)\s*-\s*(\d+(?:\.\d+)?\$)", r"\1–\2", line)
    line = re.sub(r"(\d+(?:\.\d+)?\$)\s*-\s*(\d+(?:\.\d+)?)\b", r"\1–\2", line)
    return line


def normalize_balance(line: str) -> str:
    line = re.sub(
        r"(?i)\btotal\s+balance\s*[:\-]\s*([\-+]?[0-9]+(?:\.\d+)?\$?)",
        r"Итоговый баланс: \1",
        line,
    )

    line = re.sub(
        r"(?i)\btotal\s+balance\s+left\s*[:\-]?\s*([\-+]?[0-9]+(?:\.\d+)?\$?)",
        r"Итоговый баланс: \1",
        line,
    )

    return line


def normalize_closed(line: str) -> str:
    line = re.sub(
        r"(?i)\bclosed\s+for\s+\+?([0-9]+(?:\.\d+)?\$?)",
        r"Закрыл с прибылью +\1",
        line,
    )

    line = re.sub(
        r"(?i)\bclosed\s+at\s+([0-9]+(?:\.\d+)?\$?)\s+for\s+\+?([0-9]+(?:\.\d+)?\$?)",
        r"Закрыл по \1 с прибылью +\2",
        line,
    )

    line = re.sub(
        r"(?i)\bclosed\s+at\s+([0-9]+(?:\.\d+)?)\s+for\s+\+?([0-9]+(?:\.\d+)?\$?)",
        r"Закрыл по \1 с прибылью +\2",
        line,
    )

    return line


def normalize_dca(line: str) -> str:
    line = re.sub(r"(?i)\b1st\s+dca\s+([^\n]+)", r"1-й добор: \1", line)
    line = re.sub(r"(?i)\b2nd\s+dca\s+([^\n]+)", r"2-й добор: \1", line)
    line = re.sub(r"(?i)\b3rd\s+dca\s+([^\n]+)", r"3-й добор: \1", line)
    line = re.sub(r"(?i)\b4th\s+dca\s+([^\n]+)", r"4-й добор: \1", line)
    return line


def normalize_sl_tp(line: str) -> str:
    line = re.sub(r"(?i)\bsl\s*[:\-]?\s*([^\n]+)", r"Стоп: \1", line)
    line = re.sub(r"(?i)\btp\s*[:\-]?\s*([^\n]+)", r"Тейк: \1", line)
    return line


def normalize_lost(line: str) -> str:
    line = re.sub(
        r"(?i)\blost\s*([\-+]?\s*[0-9]+(?:\.\d+)?\$?)",
        lambda m: f"Убыток: {m.group(1).replace(' ', '')}",
        line,
    )
    return line


def normalize_gains(line: str) -> str:
    line = re.sub(r"(?i)\bcrazy gains\b", "Безумная прибыль", line)
    line = re.sub(r"(?i)\bbig gains\b", "Хорошая прибыль", line)
    line = re.sub(r"(?i)\bnice gains\b", "Неплохая прибыль", line)
    return line


def normalize_gained(line: str) -> str:
    line = re.sub(
        r"(?i)\bgained\s+\+?([0-9]+(?:\.\d+)?\$?)",
        r"Прибыль: +\1",
        line,
    )
    return line


def normalize_progress(line: str) -> str:
    line = re.sub(
        r"(?i)(\d+)x\s+nearly\s+done",
        r"Почти сделано x\1",
        line,
    )

    line = re.sub(
        r"(?i)\bnearly\s+done\b",
        r"Почти сделано",
        line,
    )

    return line


def is_service_line(line: str) -> bool:
    line_low = line.lower()

    service_patterns = [
        "closed for",
        "closed at",
        "total balance",
        "total balance left",
        "1st dca",
        "2nd dca",
        "3rd dca",
        "4th dca",
        "sl",
        "tp",
        "lost",
        "gained",
        "nearly done",
        "crazy gains",
        "big gains",
        "nice gains",
    ]

    return any(p in line_low for p in service_patterns)


def is_signal_like_line(line: str) -> bool:
    line_low = line.lower()

    signal_patterns = [
        "long",
        "short",
        "bang on",
        "bang bang",
        "scalp",
        "re-entry",
        "reentry",
        "entry",
        "swing",
    ]

    has_ticker = "$" in line

    return has_ticker or any(p in line_low for p in signal_patterns)


def process_tg_line(line: str) -> str:
    line = normalize_money_ranges(line)

    if is_service_line(line):
        line = normalize_balance(line)
        line = normalize_closed(line)
        line = normalize_dca(line)
        line = normalize_sl_tp(line)
        line = normalize_lost(line)
        line = normalize_gains(line)
        line = normalize_gained(line)
        line = normalize_progress(line)
        return cleanup_whitespace(line)

    if is_signal_like_line(line):
        return cleanup_whitespace(line)

    return cleanup_whitespace(line)


def stylize_tg_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    out = []

    for line in lines:
        if not line:
            continue

        line = process_tg_line(line)

        if line:
            out.append(line)

    return "\n\n".join(out).strip()


def transform_ifttt_text_for_telegram(raw_text: str) -> Optional[str]:
    text = basic_cleanup(raw_text)
    if not text:
        return None

    text = stylize_tg_lines(text)
    text = cleanup_whitespace(text)

    return text or None


# =========================================================
# DISCORD TRANSFORM
# =========================================================

def transform_text_for_discord(raw_text: str) -> Optional[str]:
    text = basic_cleanup(raw_text)
    if not text:
        return None
    return text or None


def split_text(text: str, limit: int = 1900) -> list[str]:
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


def send_to_pipedream_text(text: str):
    parts = split_text(text)

    for part in parts:
        response = requests.post(
            PIPEDREAM_WEBHOOK_URL,
            json={
                "type": "text",
                "text": part,
            },
            timeout=30,
        )
        response.raise_for_status()

    log("Sent text to Pipedream")


def send_to_pipedream_photo(caption: str, image_bytes: bytes, filename: str = "photo.jpg"):
    encoded = base64.b64encode(image_bytes).decode("utf-8")

    response = requests.post(
        PIPEDREAM_WEBHOOK_URL,
        json={
            "type": "photo",
            "caption": caption,
            "filename": filename,
            "image_base64": encoded,
        },
        timeout=60,
    )
    response.raise_for_status()

    log("Sent photo to Pipedream")


# =========================================================
# TELEGRAM SENDERS
# =========================================================

async def send_to_target_text(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    kwargs = {
        "chat_id": TARGET_CHAT_ID,
        "message_thread_id": TARGET_MESSAGE_THREAD_ID,
        "text": text,
        "parse_mode": ParseMode.HTML,
        "disable_web_page_preview": True,
    }

    await context.bot.send_message(**kwargs)
    log("Sent text to Telegram target")


async def send_to_target_photo(
    context: ContextTypes.DEFAULT_TYPE,
    photo_file_id: str,
    caption: Optional[str] = None,
) -> None:
    kwargs = {
        "chat_id": TARGET_CHAT_ID,
        "message_thread_id": TARGET_MESSAGE_THREAD_ID,
        "photo": photo_file_id,
    }

    if caption:
        kwargs["caption"] = caption

    await context.bot.send_photo(**kwargs)
    log("Sent photo to Telegram target")


# =========================================================
# HANDLER
# =========================================================

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

    # =====================================================
    # TELEGRAM TARGET
    # =====================================================

    tg_result = transform_ifttt_text_for_telegram(raw_text)

    if msg.photo:
        largest_photo = msg.photo[-1]
        if tg_result:
            await send_to_target_photo(context, largest_photo.file_id, tg_result)
        else:
            await send_to_target_photo(context, largest_photo.file_id, None)
    else:
        if tg_result:
            await send_to_target_text(context, tg_result)
        else:
            log("Skip Telegram: empty result")

    # =====================================================
    # DISCORD / PIPEDREAM
    # =====================================================

    try:
        dc_result = transform_text_for_discord(raw_text) or ""

        if msg.photo:
            largest_photo = msg.photo[-1]
            tg_file = await context.bot.get_file(largest_photo.file_id)

            file_response = requests.get(tg_file.file_path, timeout=60)
            file_response.raise_for_status()

            send_to_pipedream_photo(
                caption=dc_result,
                image_bytes=file_response.content,
                filename="photo.jpg",
            )
        else:
            if dc_result:
                send_to_pipedream_text(dc_result)
            else:
                log("Skip Discord: empty result")

    except Exception as e:
        log("Pipedream send error:", str(e))


# =========================================================
# MAIN
# =========================================================

def main():
    validate_env()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post)
    )

    print("Unified bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
