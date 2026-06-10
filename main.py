"""
Telegram → Discord Bridge
Arki канал: перевод на RU для TG + оригинал в Discord
Selfbot с задержкой 2-3 мин → каналы с паузами 7-10 сек

Команды:
  /start
  /channels                    — список с кнопками вкл/выкл
  /addchannel <название> <id>  — добавить канал
  /removechannel <название>    — удалить канал
  /bridge                      — тумблер автопересылки
  /status                      — статус Discord
"""

import os
import re
import html
import json
import asyncio
import random
import requests
import aiohttp
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN                = os.getenv("BOT_TOKEN")
SOURCE_CHANNEL           = int(os.getenv("SOURCE_CHANNEL", "0"))
TARGET_CHAT_ID           = int(os.getenv("TARGET_CHAT_ID", "0"))
TARGET_MESSAGE_THREAD_ID = int(os.getenv("TARGET_MESSAGE_THREAD_ID", "0") or "0")
DISCORD_WEBHOOK_URL      = os.getenv("DISCORD_WEBHOOK_URL", "")
DEBUG                    = os.getenv("DEBUG", "true").lower() == "true"
DISCORD_TOKEN            = os.getenv("DISCORD_TOKEN", "")

_admin_raw = os.getenv("ADMIN_ID", "0")
ADMIN_IDS  = {int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()}

_channels_raw = os.getenv("DISCORD_CHANNELS", "")

DISCORD_API      = "https://discord.com/api/v9"
SEND_DELAY_MIN   = 7
SEND_DELAY_MAX   = 10
BRIDGE_DELAY_MIN = 120
BRIDGE_DELAY_MAX = 180


def _parse_channels_env() -> dict:
    result = {}
    for part in _channels_raw.split(","):
        part = part.strip()
        if ":" in part:
            name, cid = part.rsplit(":", 1)
            result[name.strip()] = cid.strip()
    return result


all_channels: dict[str, str] = _parse_channels_env()
active_channels: set[str]    = set(all_channels.keys())
bridge_enabled: bool          = True


def validate_env():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set")
    if SOURCE_CHANNEL == 0:
        raise ValueError("SOURCE_CHANNEL is not set")
    if TARGET_CHAT_ID == 0:
        raise ValueError("TARGET_CHAT_ID is not set")


def log(*args):
    if DEBUG:
        print(*args, flush=True)


def is_admin(update: Update) -> bool:
    return update.effective_user and update.effective_user.id in ADMIN_IDS


# ── Discord selfbot ───────────────────────────────────────────────────────────

def discord_headers() -> dict:
    return {
        "Authorization": DISCORD_TOKEN,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "X-Super-Properties": "eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiQ2hyb21lIn0=",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/channels/@me",
    }


async def discord_send_text(text: str, channel_id: str) -> bool:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            json={"content": text},
            headers=discord_headers(),
        ) as r:
            if r.status == 200:
                log(f"✅ Selfbot текст → {channel_id}")
                return True
            log(f"❌ Selfbot ошибка {r.status}: {await r.text()}")
            return False


async def discord_send_photo(file_bytes: bytes, filename: str, caption: str, channel_id: str) -> bool:
    headers = {k: v for k, v in discord_headers().items() if k != "Content-Type"}
    form = aiohttp.FormData()
    if caption:
        form.add_field("payload_json", json.dumps({"content": caption}), content_type="application/json")
    form.add_field("files[0]", file_bytes, filename=filename)
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            data=form,
            headers=headers,
        ) as r:
            if r.status == 200:
                log(f"✅ Selfbot фото → {channel_id}")
                return True
            log(f"❌ Selfbot фото ошибка {r.status}: {await r.text()}")
            return False


async def selfbot_send(text: str = "", file_bytes: bytes = None, filename: str = "photo.jpg"):
    if not DISCORD_TOKEN:
        log("⏭ Selfbot: DISCORD_TOKEN не задан")
        return
    if not bridge_enabled:
        log("⏭ Selfbot: тумблер выключен")
        return

    targets = [(n, all_channels[n]) for n in active_channels if n in all_channels]
    if not targets:
        log("⏭ Selfbot: нет активных каналов")
        return

    delay = random.uniform(BRIDGE_DELAY_MIN, BRIDGE_DELAY_MAX)
    log(f"⏳ Задержка {delay:.0f} сек ({delay/60:.1f} мин) перед selfbot...")
    await asyncio.sleep(delay)

    for i, (name, channel_id) in enumerate(targets):
        if i > 0:
            pause = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
            log(f"  ⏸ Пауза {pause:.1f} сек перед {name}")
            await asyncio.sleep(pause)
        if file_bytes:
            await discord_send_photo(file_bytes, filename, text, channel_id)
        else:
            await discord_send_text(text, channel_id)


# ── Команды ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    status = "🟢 Вкл" if bridge_enabled else "🔴 Выкл"
    await update.message.reply_text(
        f"📡 <b>Signal Bot</b>\n\n"
        f"Автопересылка: {status}\n"
        f"Активных каналов: {len(active_channels)} из {len(all_channels)}\n"
        f"Задержка: {BRIDGE_DELAY_MIN//60}–{BRIDGE_DELAY_MAX//60} мин\n\n"
        "/channels — каналы (вкл/выкл)\n"
        "/addchannel &lt;название&gt; &lt;id&gt; — добавить канал\n"
        "/removechannel &lt;название&gt; — удалить канал\n"
        "/bridge — тумблер автопересылки\n"
        "/status — статус Discord",
        parse_mode="HTML",
    )


async def cmd_channels(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not all_channels:
        await update.message.reply_text("Нет каналов.\n/addchannel &lt;название&gt; &lt;id&gt;", parse_mode="HTML")
        return
    keyboard = []
    for name in all_channels:
        mark = "✅" if name in active_channels else "⬜"
        keyboard.append([InlineKeyboardButton(f"{mark} {name}", callback_data=f"chtoggle:{name}")])
    await update.message.reply_text(
        "📋 <b>Discord каналы</b>\nНажми чтобы включить/выключить:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def cb_ch_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Нет доступа.")
        return
    name = query.data.split(":", 1)[1]
    if name in active_channels:
        active_channels.discard(name)
        await query.answer(f"Выключен: {name}")
    else:
        active_channels.add(name)
        await query.answer(f"Включён: {name}")
    keyboard = []
    for n in all_channels:
        mark = "✅" if n in active_channels else "⬜"
        keyboard.append([InlineKeyboardButton(f"{mark} {n}", callback_data=f"chtoggle:{n}")])
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_addchannel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Использование: /addchannel &lt;название&gt; &lt;channel_id&gt;", parse_mode="HTML")
        return
    channel_id = ctx.args[-1]
    name       = " ".join(ctx.args[:-1])
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{DISCORD_API}/channels/{channel_id}", headers=discord_headers()) as r:
            if r.status != 200:
                await update.message.reply_text(f"❌ Канал <code>{channel_id}</code> не найден или нет доступа.", parse_mode="HTML")
                return
            ch_name = (await r.json()).get("name", "?")
    all_channels[name] = channel_id
    active_channels.add(name)
    await update.message.reply_text(
        f"✅ Добавлен: <b>{name}</b>\nDiscord: #{ch_name}\n\n/channels — управление",
        parse_mode="HTML",
    )


async def cmd_removechannel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Использование: /removechannel &lt;название&gt;", parse_mode="HTML")
        return
    name = " ".join(ctx.args)
    if name not in all_channels:
        await update.message.reply_text(f"❌ Канал «{name}» не найден.")
        return
    del all_channels[name]
    active_channels.discard(name)
    await update.message.reply_text(f"✅ Канал «{name}» удалён.")


async def cmd_bridge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    label = "🟢 Вкл" if bridge_enabled else "🔴 Выкл"
    await update.message.reply_text(
        f"🌉 <b>Автопересылка в Discord</b>\n\nСтатус: {label}\nЗадержка: {BRIDGE_DELAY_MIN//60}–{BRIDGE_DELAY_MAX//60} мин",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="bridge_toggle")]]),
        parse_mode="HTML",
    )


async def cb_bridge_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global bridge_enabled
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Нет доступа.")
        return
    bridge_enabled = not bridge_enabled
    label = "🟢 Вкл" if bridge_enabled else "🔴 Выкл"
    await query.answer("Включено ✅" if bridge_enabled else "Выключено ❌")
    await query.edit_message_text(
        f"🌉 <b>Автопересылка в Discord</b>\n\nСтатус: {label}\nЗадержка: {BRIDGE_DELAY_MIN//60}–{BRIDGE_DELAY_MAX//60} мин",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="bridge_toggle")]]),
        parse_mode="HTML",
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not DISCORD_TOKEN:
        await update.message.reply_text("❌ DISCORD_TOKEN не задан.")
        return
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{DISCORD_API}/users/@me", headers=discord_headers()) as r:
            if r.status == 200:
                d   = await r.json()
                tag = d.get("username", "?")
                disc = d.get("discriminator", "0")
                if disc != "0":
                    tag = f"{tag}#{disc}"
                status = "🟢 Вкл" if bridge_enabled else "🔴 Выкл"
                ch_list = "\n".join(
                    f"  {'✅' if n in active_channels else '⬜'} {n}"
                    for n in all_channels
                ) or "  нет каналов"
                await update.message.reply_text(
                    f"✅ <b>Подключено</b>\n\n"
                    f"Discord: <code>{tag}</code>\n"
                    f"Автопересылка: {status}\n"
                    f"Задержка: {BRIDGE_DELAY_MIN//60}–{BRIDGE_DELAY_MAX//60} мин\n\n"
                    f"Каналы:\n{ch_list}",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(f"❌ Discord ошибка: {r.status}")


# ── Фильтр ────────────────────────────────────────────────────────────────────

def should_block_entire_post(raw_text: str) -> bool:
    if not raw_text or len(raw_text.strip()) < 3:
        return False

    text = html.unescape(raw_text).lower()
    text = re.sub(r"\s+", " ", text).strip()

    blocked_phrases = [
        # Arki watermark
        "crypto arki", "arkii trades", "arkiitrades",
        # Copy trading
        "blofin copy trading username", "copy trading username",
        "copy trading", "copy-trading", "copytrade", "copy trade",
        "copy trader", "copy my trades", "you can copy my trades",
        "join copy trading", "1st trade running in copy",
        # Биржи и партнёрки
        "blofin", "bybit referral", "okx referral",
        # Регистрация / депозит
        "profit sharing ratio", "strategy cycle",
        "sign up using", "signup & deposit", "sign up & deposit",
        "signup and deposit", "sign up and deposit",
        "how to join", "steps & conditions to follow",
        "steps and conditions to follow",
        "minimum $100", "deposit",
        "trade responsibly", "limited slots",
        "full transparency", "same entries", "same exits",
        # Реклама каналов
        "interested people can join", "people can join",
        "i will take trades here", "not including 200-2k",
        "i'm using 300$ here", "im using 300$ here",
        "join fast my telegram channel", "join fast telegram channel",
        "join my telegram channel", "join our telegram channel",
        "join telegram channel", "join my telegram group", "join telegram group",
        "telegram channel in bio", "telegram in bio",
        "twitter in bio", "x in bio",
        "share live trades", "live trades no paid/free group",
        "no paid/free group", "never dm first", "dm first",
        # Ссылки
        "https://", "http://",
    ]

    if any(phrase in text for phrase in blocked_phrases):
        return True

    if re.search(r"t\.me/\S+", text):
        return True

    if re.search(r"@[a-z_]{4,}", text):
        return True

    if re.search(r"[🎉🎊🥳]{2,}", raw_text):
        return True

    if re.search(r"\baum\b", text) and (
        "usdt" in text or "copy" in text or "strategy" in text or "trading" in text
    ):
        return True

    if "username" in text and (
        "copy" in text or "trading" in text or "blofin" in text or "arki" in text
    ):
        return True

    return False


DROP_LINE_PATTERNS = [
    r"^переслано\s+из\b.*",
    r"^forwarded\s+from\b.*",
    # Ссылки с протоколом
    r"https?://\S+",
    # Голые домены (blofin.com, bybit.com и т.д.)
    r"\b\w[\w-]*\.\w{2,}(/\S*)?\b",
    # @упоминания
    r"(^|\s)@[A-Za-z0-9_]{2,}",
    # Copy trading строки
    r"(?i)how\s+to\s+join",
    r"(?i)steps\s*[&and]+\s*conditions",
    r"(?i)sign\s*up\s+using",
    r"(?i)join\s+copy\s+trading",
    r"(?i)trade\s+responsibly",
    r"(?i)limited\s+slots",
    r"(?i)full\s+transparency",
    r"(?i)same\s+entries",
    r"(?i)same\s+exits",
]

DROP_LINE_RE = [re.compile(p, re.IGNORECASE) for p in DROP_LINE_PATTERNS]


def should_drop_line(line: str, index: int) -> bool:
    stripped = line.strip()
    low = stripped.lower()
    if not stripped:
        return False

    for pattern in DROP_LINE_RE:
        if pattern.search(stripped):
            return True

    if index == 0:
        pattern = r"^\s*\d+(?:\.\d+)?\$?\s*-\s*\d+(?:\.\d+)?\$?\s+trading\s+challenge\s*$"
        if re.match(pattern, stripped, re.IGNORECASE) and "completed" not in low:
            return True

    ad_patterns = [
        "telegram channel", "telegram group", "twitter channel",
        "twitter group", "x channel", "x group",
        "free telegram link", "join telegram in bio",
        "join my twitter", "follow my twitter", "follow me on twitter",
        "follow me on x", "bio for quick notifications",
        "thanks for supporting", "followers left",
        "announce giveaway", "send my budd", "partner.",
        "blofin", "crypto arki", "arkii trades", "arkiitrades",
    ]
    if any(p in low for p in ad_patterns):
        return True

    if "telegram" in low and ("join" in low or "channel" in low or "group" in low or "bio" in low):
        return True
    if "twitter" in low and ("join" in low or "follow" in low or "bio" in low):
        return True
    if re.search(r"\bx\b", low) and ("follow" in low or "join" in low) and ("bio" in low or "channel" in low):
        return True
    if low.startswith("→ signup") or low.startswith("→ copy"):
        return True

    return False


def remove_unwanted_lines(text: str) -> str:
    lines = text.splitlines()
    return "\n".join(line for i, line in enumerate(lines) if not should_drop_line(line, i))


def cleanup_whitespace(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r", "")
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def remove_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text)


def basic_cleanup(raw_text: str) -> str:
    text = re.sub(r"^\s*🚀\s*Новый твит от\s+.+\n?", "", raw_text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"^\s*🔗\s+https?://(twitter\.com|x\.com)/\S+\s*$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = remove_urls(text)
    text = remove_unwanted_lines(text)
    text = cleanup_whitespace(text)
    return text


# ── Трансформация для TG (перевод на RU) ─────────────────────────────────────

def normalize_for_tg(text: str) -> str:
    lines = text.splitlines()
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            out.append("")
            continue
        line = re.sub(r"(?i)\btotal\s+balance\s*[:\-]\s*([\-+]?[0-9]+(?:\.\d+)?\$?)", r"Итоговый баланс: \1", line)
        line = re.sub(r"(?i)\btotal\s+balance\s+left\s*[:\-]?\s*([\-+]?[0-9]+(?:\.\d+)?\$?)", r"Итоговый баланс: \1", line)
        line = re.sub(r"(?i)\bclosed\s+for\s+\+?([0-9]+(?:\.\d+)?\$?)", r"Закрыл с прибылью +\1", line)
        line = re.sub(r"(?i)\bclosed\s+at\s+([0-9]+(?:\.\d+)?)\s+for\s+\+?([0-9]+(?:\.\d+)?\$?)", r"Закрыл по \1 с прибылью +\2", line)
        line = re.sub(r"(?i)\bclosing\s+(\$\w+)\s+(long|short)\s+at\s+([0-9]+(?:\.\d+)?)",
                      lambda m: f"Закрываю {m.group(1).upper()} {m.group(2).lower()} по {m.group(3)}", line)
        line = re.sub(r"(?i)\b1st\s+dca\b", "1-й добор", line)
        line = re.sub(r"(?i)\b2nd\s+dca\b", "2-й добор", line)
        line = re.sub(r"(?i)\b3rd\s+dca\b", "3-й добор", line)
        line = re.sub(r"(?i)\b4th\s+dca\b", "4-й добор", line)
        line = re.sub(r"(?i)\bsl\s*[:\-]?\s*([^\n]+)", r"Стоп: \1", line)
        line = re.sub(r"(?i)\bstops?\s*[:\-]?\s*([^\n]+)", r"Стоп: \1", line)
        line = re.sub(r"(?i)\btp\s*[:\-]?\s*([^\n]+)", r"Тейк: \1", line)
        line = re.sub(r"(?i)\blost\s*([\-+]?\s*[0-9]+(?:\.\d+)?\$?)",
                      lambda m: f"Убыток: {m.group(1).replace(' ', '')}", line)
        line = re.sub(r"(?i)\bgained\s+\+?([0-9]+(?:\.\d+)?\$?)", r"Прибыль: +\1", line)
        line = re.sub(r"(?i)\bcrazy gains\b", "Безумная прибыль", line)
        line = re.sub(r"(?i)\bbig gains\b", "Хорошая прибыль", line)
        line = re.sub(r"(?i)\bnice gains\b", "Неплохая прибыль", line)
        line = re.sub(r"(?i)(\d+)x\s+nearly\s+done", r"Почти сделано x\1", line)
        line = re.sub(r"(?i)\bnearly\s+done\b", "Почти сделано", line)
        line = re.sub(r"(?i)\banother\s+(\d+)x\s+done\b", r"Ещё x\1 сделано", line)
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def transform_for_telegram(raw_text: str) -> Optional[str]:
    if not raw_text or len(raw_text.strip()) < 3:
        return None
    cleaned = basic_cleanup(raw_text)
    if not cleaned:
        return None
    return normalize_for_tg(cleaned).strip() or None


def transform_for_discord(raw_text: str) -> Optional[str]:
    if not raw_text or len(raw_text.strip()) < 3:
        return None
    cleaned = basic_cleanup(raw_text)
    return cleaned or None


# ── Discord webhook ───────────────────────────────────────────────────────────

def split_text(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
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
            current = block if len(block) <= limit else ""
            if len(block) > limit:
                for i in range(0, len(block), limit):
                    parts.append(block[i:i+limit].strip())
    if current:
        parts.append(current)
    return parts


def send_discord_webhook_text(text: str):
    if not DISCORD_WEBHOOK_URL:
        return
    for part in split_text(text):
        requests.post(DISCORD_WEBHOOK_URL, json={"content": part}, timeout=30).raise_for_status()
    log("✅ Webhook текст")


def send_discord_webhook_photo(caption: str, image_bytes: bytes):
    if not DISCORD_WEBHOOK_URL:
        return
    requests.post(
        DISCORD_WEBHOOK_URL,
        data={"payload_json": json.dumps({"content": caption or ""}, ensure_ascii=False)},
        files={"files[0]": ("photo.png", image_bytes, "image/png")},
        timeout=60,
    ).raise_for_status()
    log("✅ Webhook фото")


# ── Telegram senders ──────────────────────────────────────────────────────────

async def send_tg_text(context: ContextTypes.DEFAULT_TYPE, text: str):
    kwargs = dict(chat_id=TARGET_CHAT_ID, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    if TARGET_MESSAGE_THREAD_ID:
        kwargs["message_thread_id"] = TARGET_MESSAGE_THREAD_ID
    await context.bot.send_message(**kwargs)
    log("✅ Sent text to Telegram")


async def send_tg_photo(context: ContextTypes.DEFAULT_TYPE, file_id: str, caption: Optional[str]):
    kwargs = dict(chat_id=TARGET_CHAT_ID, photo=file_id)
    if caption:
        kwargs["caption"] = caption
    if TARGET_MESSAGE_THREAD_ID:
        kwargs["message_thread_id"] = TARGET_MESSAGE_THREAD_ID
    await context.bot.send_photo(**kwargs)
    log("✅ Sent photo to Telegram")


# ── Handler ───────────────────────────────────────────────────────────────────

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.channel_post
    if not msg:
        return
    if str(msg.chat.id) != str(SOURCE_CHANNEL):
        log(f"⏭ Skip: wrong channel ({msg.chat.id})")
        return

    raw_text = msg.text or msg.caption or ""
    log(f"\n{'='*50}\n📨 Incoming:\n{raw_text[:400]}\n{'='*50}")

    if should_block_entire_post(raw_text):
        return

    tg_text = transform_for_telegram(raw_text)
    dc_text = transform_for_discord(raw_text) or ""

    # 1. Telegram
    if msg.photo:
        await send_tg_photo(context, msg.photo[-1].file_id, tg_text)
    elif tg_text:
        await send_tg_text(context, tg_text)
    else:
        log("⏭ Skip Telegram: empty")

    # 2. Discord webhook (мгновенно)
    if DISCORD_WEBHOOK_URL:
        try:
            if msg.photo:
                tg_file = await context.bot.get_file(msg.photo[-1].file_id)
                img = requests.get(tg_file.file_path, timeout=60)
                img.raise_for_status()
                send_discord_webhook_photo(dc_text, img.content)
            elif dc_text:
                send_discord_webhook_text(dc_text)
        except Exception as e:
            log(f"❌ Webhook error: {repr(e)}")

    # 3. Discord selfbot (задержка 2-3 мин, в фоне)
    if not dc_text and not msg.photo:
        log("⏭ Skip selfbot: empty")
        return

    async def _selfbot_task():
        try:
            if msg.photo:
                tg_file = await context.bot.get_file(msg.photo[-1].file_id)
                img = requests.get(tg_file.file_path, timeout=60)
                img.raise_for_status()
                await selfbot_send(text=dc_text, file_bytes=img.content, filename="photo.jpg")
            else:
                await selfbot_send(text=dc_text)
        except Exception as e:
            log(f"❌ Selfbot task error: {repr(e)}")

    asyncio.create_task(_selfbot_task())


# ── Запуск ────────────────────────────────────────────────────────────────────

def main():
    validate_env()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",         cmd_start))
    app.add_handler(CommandHandler("channels",      cmd_channels))
    app.add_handler(CommandHandler("addchannel",    cmd_addchannel))
    app.add_handler(CommandHandler("removechannel", cmd_removechannel))
    app.add_handler(CommandHandler("bridge",        cmd_bridge))
    app.add_handler(CommandHandler("status",        cmd_status))
    app.add_handler(CallbackQueryHandler(cb_ch_toggle,     pattern=r"^chtoggle:"))
    app.add_handler(CallbackQueryHandler(cb_bridge_toggle, pattern=r"^bridge_toggle$"))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))

    print("🚀 Signal filter bot started", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
