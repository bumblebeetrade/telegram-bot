import re
import html
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
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = "8645637817:AAFCNSOmk45verNL9sJOb1JqinjRESJncIE"

SOURCE_CHANNEL = -1003529309686
TARGET_CHAT_ID = -1003787362562
TARGET_MESSAGE_THREAD_ID = 2

DEBUG = True

# =========================================================


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


def process_line(line: str) -> str:
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


def stylize_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    out = []

    for line in lines:
        if not line:
            continue

        line = process_line(line)

        if line:
            out.append(line)

    return "\n\n".join(out).strip()


def transform_ifttt_text(raw_text: str) -> Optional[str]:
    text = basic_cleanup(raw_text)
    if not text:
        return None

    text = stylize_lines(text)
    text = cleanup_whitespace(text)

    return text or None


async def send_to_target(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        message_thread_id=TARGET_MESSAGE_THREAD_ID,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    print("Sent text-only message to target")


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.channel_post
    if not msg:
        return

    chat = msg.chat
    raw_text = msg.text or msg.caption or ""

    print("Incoming channel:", chat.id, "|", chat.title)
    print("Raw text:\n", raw_text)

    if str(chat.id) != str(SOURCE_CHANNEL):
        print("Skip: not source channel")
        return

    result = transform_ifttt_text(raw_text)
    if not result:
        print("Skip: empty result after transform")
        return

    print("Final text:\n", result)

    await send_to_target(context, result)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post)
    )

    print("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()