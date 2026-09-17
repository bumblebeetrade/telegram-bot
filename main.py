"""
Telegram → Discord Bridge

Языки таргетов:
  RU  — только TG #1 (Crypto Phoenix)
  EN  — все остальные: TG #2 (Heaven), TG #3 (мой аккаунт),
        Discord webhook Bee, Rebel Angels, selfbot каналы

Selfbot с задержкой 48-72 сек → каналы с паузами 7-10 сек →
webhook Rebel Angels → сводка в самом конце
Плюс таргеты от лица моих аккаунтов (Telethon)

Команды:
  /start
  /channels                    — список с кнопками вкл/выкл
  /addchannel <название> <id>  — добавить канал
  /removechannel <название>    — удалить канал
  /bridge                      — тумблер автопересылки
  /status                      — статус Discord
  /checkchats                  — диагностика доступа к TG таргетам
  /mychats [слово]             — ID всех групп подключённых аккаунтов
  /posts                       — последние посты, удаление разом во всех таргетах
"""

import os
import io
import re
import html
import json
import asyncio
import random
import requests
import aiohttp
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from telethon import TelegramClient
from telethon.sessions import StringSession

BOT_TOKEN                  = os.getenv("BOT_TOKEN")
BOT_TOKEN_2                = os.getenv("BOT_TOKEN_2", "")  # Heaven — для TG #2

SOURCE_CHANNEL             = int(os.getenv("SOURCE_CHANNEL", "0"))

TARGET_CHAT_ID             = int(os.getenv("TARGET_CHAT_ID", "0"))
TARGET_MESSAGE_THREAD_ID   = int(os.getenv("TARGET_MESSAGE_THREAD_ID", "0") or "0")

TARGET_CHAT_ID_2           = int(os.getenv("TARGET_CHAT_ID_2", "0"))
TARGET_MESSAGE_THREAD_ID_2 = int(os.getenv("TARGET_MESSAGE_THREAD_ID_2", "0") or "0")

# ── Таргеты от лица моего аккаунта (Telethon) ────────────────────────────────
TG_API_ID                  = int(os.getenv("TG_API_ID", "0") or "0")
TG_API_HASH                = os.getenv("TG_API_HASH", "")
TG_USER_SESSION            = os.getenv("TG_USER_SESSION", "")

# Список чатов через запятую. Каждый: chat_id  или  chat_id:topic_id
#   пример: -1002338492731,-1001234567890:5,@my_group
# USER_TARGET_CHAT / USER_TARGET_TOPIC_ID оставлены для совместимости
# (если задан старый одиночный — он добавляется к списку).
USER_TARGET_CHATS          = os.getenv("USER_TARGET_CHATS", "")
USER_TARGET_CHAT           = os.getenv("USER_TARGET_CHAT", "")
USER_TARGET_TOPIC_ID       = int(os.getenv("USER_TARGET_TOPIC_ID", "0") or "0")
USER_LABEL                 = os.getenv("USER_LABEL", "BumbleBee")

# Второй аккаунт (Ivan) — своя session-строка и свой список чатов,
# формат USER_TARGET_CHATS_2 тот же: chat_id / chat_id:topic_id / @username
TG_USER_SESSION_2          = os.getenv("TG_USER_SESSION_2", "")
USER_TARGET_CHATS_2        = os.getenv("USER_TARGET_CHATS_2", "")
USER_LABEL_2               = os.getenv("USER_LABEL_2", "Ivan")

DISCORD_WEBHOOK_URL        = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_WEBHOOK_URL_2      = os.getenv("DISCORD_WEBHOOK_URL_2", "")
DEBUG                      = os.getenv("DEBUG", "true").lower() == "true"
DISCORD_TOKEN              = os.getenv("DISCORD_TOKEN", "")

# ── Сводка о доставке ────────────────────────────────────────────────────────
REPORT_ENABLED             = os.getenv("REPORT_ENABLED", "true").lower() == "true"
REPORT_CHAT_ID             = int(os.getenv("REPORT_CHAT_ID", "0") or "0")
REPORT_TITLE               = os.getenv("REPORT_TITLE", "Bee")
REPORT_ICON                = os.getenv("REPORT_ICON", "🐝")
REPORT_BLOCKED             = os.getenv("REPORT_BLOCKED", "true").lower() == "true"
REPORT_TZ_OFFSET           = int(os.getenv("REPORT_TZ_OFFSET", "3") or "3")  # GMT+3

_admin_raw = os.getenv("ADMIN_ID", "0")
ADMIN_IDS  = {int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()}

_channels_raw = os.getenv("DISCORD_CHANNELS", "")

DISCORD_API      = "https://discord.com/api/v9"
SEND_DELAY_MIN   = 7
SEND_DELAY_MAX   = 10
BRIDGE_DELAY_MIN = 48
BRIDGE_DELAY_MAX = 72

# Slow mode на Discord-серверах (например Bulk Trade — 5 сек).
# Повторяем ТОЛЬКО при 429; 403/404 повторять нельзя.
SLOWMODE_RETRIES      = int(os.getenv("SLOWMODE_RETRIES", "4") or "4")
SLOWMODE_MAX_WAIT     = float(os.getenv("SLOWMODE_MAX_WAIT", "30") or "30")
SLOWMODE_FALLBACK_WAIT = 5.0

# Скачивание фото из Bot API: сколько попыток при таймаутах Telegram
# (первый заход короткий, чтобы не задерживать конвейер; если не вышло —
# вторая волна через LATE_PHOTO_WAIT сек досылает всем, кому нужны байты)
PHOTO_DL_ATTEMPTS     = int(os.getenv("PHOTO_DL_ATTEMPTS", "3") or "3")
LATE_PHOTO_WAIT       = int(os.getenv("LATE_PHOTO_WAIT", "60") or "60")

# Сколько последних пересланных постов помнить для /posts (удаление везде).
# Хранится только в памяти процесса: несколько КБ, диск не используется.
POSTS_KEEP            = int(os.getenv("POSTS_KEEP", "15") or "15")


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

# Все подключённые Telethon-аккаунты:
# [{"name": "BumbleBee", "client": ..., "targets": [(entity, topic_id, label), ...]}, ...]
user_accounts: list = []

# Последние пересланные посты: каждый — «связка» ID отправленных сообщений
# по всем таргетам, чтобы /posts мог удалить пост разом везде.
# deque с maxlen: старые связки вытесняются сами, память не растёт.
sent_posts: deque = deque(maxlen=POSTS_KEEP)
_post_seq: int = 0


def new_bundle(preview: str) -> dict:
    global _post_seq
    _post_seq += 1
    b = {
        "uid": _post_seq,
        "time": _now().strftime("%d.%m %H:%M"),
        "preview": (preview or "фото")[:60],
        "items": [],        # [{"kind","label","refs"}]
        "deleted": False,   # True — пост удалён (и отменяет отложенные отправки)
    }
    sent_posts.append(b)
    return b


def bundle_add(bundle: Optional[dict], kind: str, label: str, *refs):
    if bundle is not None:
        bundle["items"].append({"kind": kind, "label": label, "refs": refs})


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


# ── Сводка о доставке ────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=REPORT_TZ_OFFSET)))


def _hm() -> str:
    return _now().strftime("%H:%M")


class Report:
    """Копит результаты по всем таргетам и рендерит одно сообщение в конце."""

    def __init__(self, preview: str = ""):
        self.sections: dict[str, list[tuple[str, bool, str, str]]] = {}
        self.order: list[str] = []
        self.preview = preview
        self.started = _now()

    def add(self, section: str, label: str, ok: bool, note: str = ""):
        if section not in self.sections:
            self.sections[section] = []
            self.order.append(section)
        self.sections[section].append((label, ok, note, _hm()))

    def render(self) -> str:
        total = sum(len(v) for v in self.sections.values())
        good  = sum(1 for v in self.sections.values() for _, ok, _, _ in v if ok)

        icon = REPORT_ICON if good == total else "⚠️"
        head = f"{icon} <b>{html.escape(REPORT_TITLE)}</b> — доставлено {good}/{total}"

        lines = [head]
        if self.preview:
            lines.append(f"<i>{html.escape(self.preview)}</i>")

        for section in self.order:
            lines.append(f"\n<b>{section}</b>")
            for label, ok, note, ts in self.sections[section]:
                mark = "✅" if ok else "❌"
                row  = f"{mark} {html.escape(label)} · {ts}"
                if note and not ok:
                    row += f"\n     └ <i>{html.escape(note)}</i>"
                lines.append(row)

        elapsed = (_now() - self.started).total_seconds()
        if elapsed >= 60:
            lines.append(f"\n<i>заняло {elapsed/60:.1f} мин</i>")
        else:
            lines.append(f"\n<i>заняло {elapsed:.0f} сек</i>")

        return "\n".join(lines)


def _report_targets() -> list[int]:
    targets = list(ADMIN_IDS)
    if REPORT_CHAT_ID:
        targets.append(REPORT_CHAT_ID)
    return targets


async def _deliver_report(bot: Bot, text: str):
    for chat_id in _report_targets():
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as e:
            log(f"❌ Report → {chat_id}: {repr(e)}")


async def send_report(bot: Bot, report: Report):
    """Шлём сводку админам в личку и/или в отдельный чат."""
    if not REPORT_ENABLED:
        return
    await _deliver_report(bot, report.render())


async def send_blocked_report(bot: Bot, raw_text: str, reason: str):
    """Уведомление о посте, который целиком срезали фильтры."""
    if not REPORT_ENABLED or not REPORT_BLOCKED:
        return
    preview = " / ".join(
        line.strip() for line in raw_text.splitlines() if line.strip()
    )[:200] or "(без текста)"
    text = (
        f"🚫 <b>Пост отфильтрован</b> · {_hm()}\n"
        f"<i>{html.escape(preview)}</i>\n\n"
        f"Причина: <code>{html.escape(reason)}</code>"
    )
    await _deliver_report(bot, text)


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


def _retry_after(body: str) -> float:
    """Сколько ждать — берём из ответа самого Discord + небольшой разброс,
    чтобы параллельные отправки не ломились в канал в одну и ту же секунду."""
    try:
        val = float(json.loads(body).get("retry_after", SLOWMODE_FALLBACK_WAIT))
    except Exception:
        val = SLOWMODE_FALLBACK_WAIT
    return min(max(val, 1.0) + random.uniform(0.5, 2.0), SLOWMODE_MAX_WAIT)


async def _discord_send(build_request, channel_id: str, kind: str) -> bool:
    """
    Отправка в канал selfbot'ом.

    Повтор ТОЛЬКО на 429 (slow mode) и ровно на столько, сколько велит
    Discord. Любая другая ошибка — 403 «нет прав», 404 «нет канала»,
    обрыв связи — отказ сразу: повторять бессмысленно, а на обрыве ещё
    и опасно (сообщение могло уйти, повтор дал бы дубль).
    """
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    try:
        for attempt in range(1, SLOWMODE_RETRIES + 1):
            # тело запроса собираем заново на каждую попытку:
            # aiohttp.FormData одноразовая, повторно её отправить нельзя
            kwargs = build_request()
            async with aiohttp.ClientSession() as s:
                async with s.post(url, **kwargs) as r:
                    if r.status == 200:
                        suffix = f" (с {attempt}-й попытки)" if attempt > 1 else ""
                        log(f"✅ Selfbot {kind} → {channel_id}{suffix}")
                        # id сообщения нужен для /posts (удаление);
                        # truthy-строка, так что bool(ok) у вызывающих не ломается
                        try:
                            return (await r.json()).get("id") or True
                        except Exception:
                            return True

                    body = await r.text()

                    if r.status != 429:
                        log(f"❌ Selfbot {kind} ошибка {r.status}: {body[:200]}")
                        return False

                    wait = _retry_after(body)
                    if attempt >= SLOWMODE_RETRIES:
                        log(f"❌ Selfbot {kind} → {channel_id}: slow mode, "
                            f"{SLOWMODE_RETRIES} попыток исчерпаны — отмена")
                        return False
                    log(f"⏳ Selfbot {kind} → {channel_id}: slow mode, жду {wait:.1f} сек "
                        f"(попытка {attempt}/{SLOWMODE_RETRIES})")
                    await asyncio.sleep(wait)
        return False
    except Exception as e:
        log(f"❌ Selfbot {kind} → {channel_id}: {repr(e)}")
        return False


async def discord_send_text(text: str, channel_id: str) -> bool:
    def build():
        return dict(json={"content": text}, headers=discord_headers())
    return await _discord_send(build, channel_id, "текст")


async def discord_send_photo(file_bytes: bytes, filename: str, caption: str, channel_id: str) -> bool:
    headers = {k: v for k, v in discord_headers().items() if k != "Content-Type"}

    def build():
        form = aiohttp.FormData()
        if caption:
            form.add_field("payload_json", json.dumps({"content": caption}), content_type="application/json")
        form.add_field("files[0]", file_bytes, filename=filename)
        return dict(data=form, headers=headers)

    return await _discord_send(build, channel_id, "фото")


def _send_webhook_text(url: str, text: str) -> list:
    """Возвращает id отправленных сообщений (wait=true) — для /posts."""
    ids = []
    if not url:
        return ids
    for part in split_text(text):
        r = requests.post(url, params={"wait": "true"}, json={"content": part}, timeout=30)
        r.raise_for_status()
        try:
            ids.append(r.json()["id"])
        except Exception:
            pass
    return ids


def _send_webhook_photo(url: str, caption: str, image_bytes: bytes) -> list:
    if not url:
        return []
    r = requests.post(
        url,
        params={"wait": "true"},
        data={"payload_json": json.dumps({"content": caption or ""}, ensure_ascii=False)},
        files={"files[0]": ("photo.png", image_bytes, "image/png")},
        timeout=60,
    )
    r.raise_for_status()
    try:
        return [r.json()["id"]]
    except Exception:
        return []


async def download_photo(bot: Bot, file_id: str, attempts: int = 0) -> Optional[bytes]:
    """
    Скачиваем фото с ретраями и растущими паузами. get_file иногда
    таймаутит на стороне Telegram по несколько минут подряд, поэтому
    паузы между попытками растут (3→6→12→... сек), а таймауты щедрые.
    """
    attempts = attempts or PHOTO_DL_ATTEMPTS
    delay = 3.0
    for attempt in range(1, attempts + 1):
        try:
            tg_file = await bot.get_file(file_id, read_timeout=60, connect_timeout=20)
            img = await asyncio.to_thread(requests.get, tg_file.file_path, timeout=120)
            img.raise_for_status()
            suffix = f" (с {attempt}-й попытки)" if attempt > 1 else ""
            log(f"✅ Фото скачано{suffix}")
            return img.content
        except Exception as e:
            log(f"⚠️ Попытка {attempt}/{attempts} скачать фото: {repr(e)}")
            if attempt < attempts:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
    log(f"❌ Не удалось скачать фото после {attempts} попыток")
    return None


async def delayed_send(text: str, img_bytes: Optional[bytes],
                       report: Optional[Report] = None, bot: Optional[Bot] = None,
                       photo_file_id: Optional[str] = None,
                       bundle: Optional[dict] = None):
    """Задержка 48-72 сек → selfbot каналы (паузы 7-10 сек) → пауза → Rebel Angels → сводка."""

    def cancelled() -> bool:
        return bool(bundle and bundle["deleted"])

    async def finish():
        if report and bot:
            await send_report(bot, report)

    if not bridge_enabled:
        if report:
            report.add("🤖 Selfbot", "автопересылка выключена", False, "/bridge")
        await finish()
        return

    delay = random.uniform(BRIDGE_DELAY_MIN, BRIDGE_DELAY_MAX)
    log(f"⏳ Задержка {delay:.0f} сек ({delay/60:.1f} мин)...")
    await asyncio.sleep(delay)

    if not bridge_enabled:
        if report:
            report.add("🤖 Selfbot", "автопересылка выключена", False, "/bridge")
        await finish()
        return

    # фото могло не скачаться сразу (Telegram таймаутил) — спустя
    # 2-3 мин задержки пробуем ещё раз, обычно API уже отвечает
    if img_bytes is None and photo_file_id and bot:
        log("🔁 Повторная попытка скачать фото для отложенной отправки...")
        img_bytes = await download_photo(bot, photo_file_id)

    if not text and not img_bytes:
        log("⏭ Delayed: фото так и не скачалось, текста нет — пропуск")
        await finish()
        return

    if cancelled():
        log("🛑 Пост удалён через /posts — отложенная отправка отменена")
        return

    # 1. Selfbot-каналы (паузы 7-10 сек между ними)
    if DISCORD_TOKEN:
        targets = [(n, all_channels[n]) for n in active_channels if n in all_channels]
        if not targets:
            log("⏭ Selfbot: нет активных каналов")
        for i, (name, channel_id) in enumerate(targets):
            if cancelled():
                log("🛑 Пост удалён через /posts — остаток selfbot-очереди отменён")
                return
            if i > 0:
                pause = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
                log(f"  ⏸ Пауза {pause:.1f} сек перед {name}")
                await asyncio.sleep(pause)
            try:
                if img_bytes:
                    ok = await discord_send_photo(img_bytes, "photo.jpg", text, channel_id)
                else:
                    ok = await discord_send_text(text, channel_id)
            except Exception as e:
                log(f"❌ Selfbot {name} error: {repr(e)}")
                ok = False
            if isinstance(ok, str):
                bundle_add(bundle, "selfbot", f"Selfbot {name}", channel_id, ok)
            if report: report.add("🤖 Selfbot", name, bool(ok))

    # 2. Webhook Rebel Angels — после selfbot-каналов
    if DISCORD_WEBHOOK_URL_2 and not cancelled():
        pause = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
        log(f"⏸ Пауза {pause:.1f} сек перед Rebel Angels...")
        await asyncio.sleep(pause)
        try:
            ids = []
            if img_bytes:
                ids = _send_webhook_photo(DISCORD_WEBHOOK_URL_2, text, img_bytes)
                log("✅ Webhook Rebel Angels фото")
            elif text:
                ids = _send_webhook_text(DISCORD_WEBHOOK_URL_2, text)
                log("✅ Webhook Rebel Angels текст")
            for mid in ids:
                bundle_add(bundle, "webhook", "Webhook Rebel Angels", DISCORD_WEBHOOK_URL_2, mid)
            if report: report.add("🌐 Discord webhook", "Rebel Angels", True)
        except Exception as e:
            log(f"❌ Webhook Rebel Angels error: {repr(e)}")
            if report: report.add("🌐 Discord webhook", "Rebel Angels", False, str(e)[:60])

    # 3. Сводка о доставке — в самом конце, когда всё отправлено
    if not cancelled():
        await finish()


# ── Команды ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    status = "🟢 Вкл" if bridge_enabled else "🔴 Выкл"
    await update.message.reply_text(
        f"📡 <b>Signal Bot</b>\n\n"
        f"Автопересылка: {status}\n"
        f"Активных каналов: {len(active_channels)} из {len(all_channels)}\n"
        f"Задержка: {BRIDGE_DELAY_MIN}–{BRIDGE_DELAY_MAX} сек\n\n"
        "/channels — каналы (вкл/выкл)\n"
        "/addchannel &lt;название&gt; &lt;id&gt; — добавить канал\n"
        "/removechannel &lt;название&gt; — удалить канал\n"
        "/bridge — тумблер автопересылки\n"
        "/status — статус Discord\n"
        "/checkchats — диагностика TG таргетов\n"
        "/mychats — ID всех групп аккаунтов\n"
        "/posts — последние посты (удалить везде)",
        parse_mode="HTML",
    )


async def cmd_checkchats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    results = []

    # TG #1 — основной бот
    if TARGET_CHAT_ID:
        try:
            chat = await ctx.bot.get_chat(TARGET_CHAT_ID)
            title = chat.title or chat.first_name or "?"
            results.append(f"✅ TG #1: <code>{TARGET_CHAT_ID}</code> (thread {TARGET_MESSAGE_THREAD_ID}) — {html.escape(title)}")
        except Exception as e:
            results.append(f"❌ TG #1: <code>{TARGET_CHAT_ID}</code> (thread {TARGET_MESSAGE_THREAD_ID}) — {html.escape(repr(e))}")
    else:
        results.append("⚪ TG #1: не задан")

    # TG #2 — Heaven бот
    if TARGET_CHAT_ID_2 and BOT_TOKEN_2:
        try:
            bot2 = Bot(token=BOT_TOKEN_2)
            chat = await bot2.get_chat(TARGET_CHAT_ID_2)
            title = chat.title or chat.first_name or "?"
            results.append(f"✅ TG #2 (Heaven): <code>{TARGET_CHAT_ID_2}</code> (thread {TARGET_MESSAGE_THREAD_ID_2}) — {html.escape(title)}")
        except Exception as e:
            results.append(f"❌ TG #2 (Heaven): <code>{TARGET_CHAT_ID_2}</code> (thread {TARGET_MESSAGE_THREAD_ID_2}) — {html.escape(repr(e))}")
    elif TARGET_CHAT_ID_2:
        results.append("⚪ TG #2: BOT_TOKEN_2 не задан")
    else:
        results.append("⚪ TG #2: не задан")

    # Аккаунт-таргеты (Telethon) — все подключённые аккаунты
    if user_accounts:
        for acc in user_accounts:
            try:
                me = await acc["client"].get_me()
                handle = html.escape(me.username or me.first_name or "?")
                results.append(f"✅ Аккаунт {html.escape(acc['name'])} (@{handle}) — чатов: {len(acc['targets'])}")
                for _, topic, label in acc["targets"]:
                    extra = f" (topic {topic})" if topic else ""
                    results.append(f"   • {html.escape(label)}{extra}")
            except Exception as e:
                results.append(f"❌ Аккаунт {html.escape(acc['name'])}: {html.escape(repr(e))}")
    elif _configured_accounts():
        results.append("❌ Аккаунт-таргеты: не подключены — проверь TG_API_ID / TG_API_HASH / TG_USER_SESSION(_2)")
    else:
        results.append("⚪ Аккаунт-таргеты: не заданы")

    await update.message.reply_text(
        "🔍 <b>Проверка доступа к чатам:</b>\n\n" + "\n".join(results),
        parse_mode="HTML",
    )


async def _reply_chunks(message, lines: list[str], limit: int = 3500):
    """Длинный список режем на сообщения — у Telegram лимит 4096 символов."""
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > limit:
            await message.reply_text(chunk, parse_mode="HTML")
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await message.reply_text(chunk, parse_mode="HTML")


async def cmd_mychats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Список групп/каналов каждого подключённого аккаунта вместе с ID —
    чтобы не искать ID вручную. /mychats <слово> фильтрует по названию.
    """
    if not is_admin(update):
        return

    if not user_accounts:
        await update.message.reply_text("⚪ Нет подключённых аккаунтов.")
        return

    needle = " ".join(ctx.args).strip().lower() if ctx.args else ""

    for acc in user_accounts:
        # id уже настроенных таргетов — чтобы пометить их галочкой
        known = {getattr(e, "id", None) for e, _, _ in acc["targets"]}

        header = f"📋 <b>{html.escape(acc['name'])}</b>"
        if needle:
            header += f" — поиск «{html.escape(needle)}»"
        lines, shown = [header, ""], 0

        try:
            async for d in acc["client"].iter_dialogs():
                if d.is_user:
                    continue
                title = d.name or "?"
                if needle and needle not in title.lower():
                    continue
                shown += 1
                mark  = "✅" if getattr(d.entity, "id", None) in known else "▫️"
                forum = " 🧵" if getattr(d.entity, "forum", False) else ""
                lines.append(f"{mark} {html.escape(title)}{forum}\n<code>{d.id}</code>")
        except Exception as e:
            lines.append(f"❌ Ошибка чтения диалогов: {html.escape(repr(e))}")

        if shown == 0:
            lines.append("(ничего не найдено)")
        else:
            lines.append(f"\n<i>всего: {shown} · ✅ уже в таргетах · 🧵 форум (нужен topic id)</i>")

        await _reply_chunks(update.message, lines)


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
        f"🌉 <b>Автопересылка в Discord</b>\n\nСтатус: {label}\nЗадержка: {BRIDGE_DELAY_MIN}–{BRIDGE_DELAY_MAX} сек",
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
        f"🌉 <b>Автопересылка в Discord</b>\n\nСтатус: {label}\nЗадержка: {BRIDGE_DELAY_MIN}–{BRIDGE_DELAY_MAX} сек",
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
                if user_accounts:
                    user_state = "🟢 " + ", ".join(
                        f"{a['name']}: {len(a['targets'])}" for a in user_accounts
                    )
                else:
                    user_state = "⚪ не задан/не подключён"
                await update.message.reply_text(
                    f"✅ <b>Подключено</b>\n\n"
                    f"Discord: <code>{tag}</code>\n"
                    f"Аккаунты (чатов): {user_state}\n"
                    f"Автопересылка: {status}\n"
                    f"Задержка: {BRIDGE_DELAY_MIN}–{BRIDGE_DELAY_MAX} сек\n\n"
                    f"Каналы:\n{ch_list}",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(f"❌ Discord ошибка: {r.status}")


# ── /posts: последние пересланные посты, удаление разом везде ─────────────────

def _find_bundle(uid: int) -> Optional[dict]:
    for b in sent_posts:
        if b["uid"] == uid:
            return b
    return None


async def _delete_everywhere(bundle: dict, bot: Bot) -> list[tuple]:
    """
    Удаляем все сообщения связки по всем таргетам. Флаг deleted ставим
    ПЕРВЫМ делом — он же отменяет ещё не отправленные отложенные волны
    (delayed_send / late_photo_wave проверяют его перед каждой отправкой).
    """
    bundle["deleted"] = True
    results = []
    for it in bundle["items"]:
        kind, label, refs = it["kind"], it["label"], it["refs"]
        ok, note = False, ""
        try:
            if kind == "bot1":
                await bot.delete_message(chat_id=refs[0], message_id=refs[1])
                ok = True
            elif kind == "bot2":
                await Bot(token=BOT_TOKEN_2).delete_message(chat_id=refs[0], message_id=refs[1])
                ok = True
            elif kind == "user":
                acc = next((a for a in user_accounts if a["name"] == refs[0]), None)
                if acc is None:
                    note = "аккаунт не подключён"
                else:
                    await acc["client"].delete_messages(refs[1], [refs[2]])
                    ok = True
            elif kind == "webhook":
                r = await asyncio.to_thread(
                    requests.delete, f"{refs[0]}/messages/{refs[1]}", timeout=30)
                ok = r.status_code in (200, 204)
                if not ok:
                    note = f"HTTP {r.status_code}"
            elif kind == "selfbot":
                async with aiohttp.ClientSession() as s:
                    async with s.delete(
                        f"{DISCORD_API}/channels/{refs[0]}/messages/{refs[1]}",
                        headers=discord_headers(),
                    ) as r:
                        ok = r.status in (200, 204)
                        if not ok:
                            note = f"HTTP {r.status}"
        except Exception as e:
            note = str(e)[:60]
        log(f"{'🗑' if ok else '❌'} Удаление {label}: {'ok' if ok else note}")
        results.append((label, ok, note))
        await asyncio.sleep(0.4)   # мягкий темп, чтобы не ловить лимиты API
    return results


async def cmd_posts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not sent_posts:
        await update.message.reply_text("📭 Память постов пуста (после рестарта она обнуляется).")
        return
    keyboard = []
    for b in reversed(sent_posts):
        mark = "☑️" if b["deleted"] else "🗑"
        keyboard.append([InlineKeyboardButton(
            f"{mark} {b['time']} · {b['preview'][:32]}",
            callback_data=f"pdel:{b['uid']}",
        )])
    await update.message.reply_text(
        f"🧹 <b>Последние посты</b> ({len(sent_posts)}/{POSTS_KEEP})\n"
        "Нажми на пост, чтобы удалить его во всех таргетах:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def cb_post_del(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Нет доступа.")
        return
    uid = int(query.data.split(":", 1)[1])
    b = _find_bundle(uid)
    if b is None:
        await query.answer("Поста уже нет в памяти.")
        return
    if b["deleted"]:
        await query.answer("Уже удалён.")
        return
    await query.answer()
    await query.edit_message_text(
        f"❗️ Удалить этот пост из {len(b['items'])} мест?\n\n"
        f"<i>{html.escape(b['preview'])}</i> · {b['time']}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да, удалить везде", callback_data=f"pdelc:{uid}"),
            InlineKeyboardButton("↩️ Отмена",           callback_data=f"pdelx:{uid}"),
        ]]),
        parse_mode="HTML",
    )


async def cb_post_del_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Нет доступа.")
        return
    uid = int(query.data.split(":", 1)[1])
    b = _find_bundle(uid)
    if b is None:
        await query.answer("Поста уже нет в памяти.")
        return
    if b["deleted"]:
        await query.answer("Уже удалён.")
        return
    await query.answer("🗑 Удаляю...")
    results = await _delete_everywhere(b, ctx.bot)
    good = sum(1 for _, ok, _ in results if ok)
    lines = [
        f"🗑 <b>Удаление поста</b> — {good}/{len(results)}",
        f"<i>{html.escape(b['preview'])}</i> · {b['time']}",
        "",
    ]
    for label, ok, note in results:
        row = f"{'✅' if ok else '❌'} {html.escape(label)}"
        if note and not ok:
            row += f" — <i>{html.escape(note)}</i>"
        lines.append(row)
    if not results:
        lines.append("(отправленных сообщений не было — отменены только отложенные волны)")
    await query.edit_message_text("\n".join(lines), parse_mode="HTML")


async def cb_post_del_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Нет доступа.")
        return
    await query.answer("Отменено")
    await query.edit_message_text("↩️ Отменено. /posts — показать список снова.")


# ── Фильтр ────────────────────────────────────────────────────────────────────

def block_reason(raw_text: str) -> Optional[str]:
    """Причина, по которой пост режется целиком. None — пост проходит."""
    if not raw_text or len(raw_text.strip()) < 3:
        return None

    text = html.unescape(raw_text).lower()
    text = re.sub(r"\s+", " ", text).strip()

    blocked_phrases = [
        "crypto arki", "arkii trades", "arkiitrades",
        "blofin copy trading username", "copy trading username",
        "copy trading", "copy-trading", "copytrade", "copy trade",
        "copy trader", "copy my trades", "you can copy my trades",
        "join copy trading", "1st trade running in copy",
        "blofin", "bybit referral", "okx referral",
        "profit sharing ratio", "strategy cycle",
        "sign up using", "signup & deposit", "sign up & deposit",
        "signup and deposit", "sign up and deposit",
        "how to join", "steps & conditions to follow",
        "steps and conditions to follow",
        "minimum $100", "deposit",
        "trade responsibly", "limited slots",
        "full transparency", "same entries", "same exits",
        "paid group",
        "i will add them",
        "add them in my",
        "whoever joined",
        "hope still you believe",
        "1-2 trades per day",
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
        "telegram",
        "https://", "http://",

        # ── Биржи и платформы (в обычных сигналах не упоминаются) ──
        "bitunix", "binance", "bybit", "okx", "bitget", "kucoin", "mexc",
        "gate.io", "gateio", "htx", "huobi", "bingx", "phemex", "deribit",
        "coinex", "kraken", "coinbase", "bitmex", "bitfinex", "lbank",
        "pionex", "toobit", "weex", "bitrue", "whitebit", "hyperliquid",
        "bitmart", "poloniex", "bitstamp", "upbit", "bithumb",
        "platform", "exchange",

        # ── Рефералки, промокоды, комиссии ──
        "referral", "referal", "ref link", "ref code",
        "promo code", "promocode", "bonus code", "use code", "my code",
        "affiliate", "commission", "management fee",
        "profit share", "pnl share", "bonus",

        # ── VIP / платные группы, подписки, обучение ──
        "vip signal", "vip group", "vip channel", "vip member",
        "premium group", "premium signal", "premium channel",
        "subscription", "subscribe",
        "mentorship", "webinar", "masterclass", "academy", "trading course",
        "lifetime access", "free access", "free signal", "free trial",
        "prop firm", "funded account",

        # ── Призывы написать / вступить ──
        "dm me", "pm me", "inbox me", "message me", "text me", "contact me",
        "reach out", "link in bio", "check bio", "bio link", "in bio",
        "join now", "join us", "join here", "invite link", "invite you",
        "my channel", "my group", "our channel", "our group",
        "trade with me", "trade with us", "copy me", "follow my trades",

        # ── Регистрация на площадках ──
        "sign up", "signup", "register", "registration",
        "create account", "open account", "new account",

        # ── Соцсети и мессенджеры ──
        "discord.gg", "whatsapp", "instagram", "youtube", "tiktok",
        "facebook", "snapchat",

        # ── Маркетинговое давление ──
        # NB: слова уточнены — голые "hurry" / "guarantee" / "last chance"
        # ловили обычные комментарии трейдера («I m not in Hurry...»)
        "guaranteed profit", "guaranteed return", "risk free",
        "slots left", "seats left", "spots left",
        "last chance to join", "limited time offer", "hurry up",
        "click here", "swipe up", "tap the link",
        "double your", "10x your",
    ]

    for phrase in blocked_phrases:
        if phrase in text:
            return f"стоп-фраза «{phrase}»"

    if re.search(r"t\.me/\S+", text):
        return "ссылка t.me"

    if re.search(r"@[a-z_]{4,}", text):
        return "упоминание @username"

    # домен без http:// — «bitunix.com», «partner.blofin»
    if re.search(
        r"\b[a-z0-9][a-z0-9-]*\.(com|net|org|io|me|co|xyz|app|link|gg|top|vip|pro|site|online|club|finance)\b",
        text,
    ):
        return "домен без http"

    if re.search(r"[🎉🎊🥳]{2,}", raw_text):
        return "эмодзи-спам"

    if re.search(r"[💰💵💸🤑]{2,}", raw_text):
        return "денежный эмодзи-спам"

    if re.search(r"\baum\b", text) and (
        "usdt" in text or "copy" in text or "strategy" in text or "trading" in text
    ):
        return "AUM + копитрейд"

    if "username" in text and (
        "copy" in text or "trading" in text or "blofin" in text or "arki" in text
    ):
        return "username + копитрейд"

    return None


def should_block_entire_post(raw_text: str) -> bool:
    return block_reason(raw_text) is not None


DROP_LINE_PATTERNS = [
    r"^переслано\s+из\b.*",
    r"^forwarded\s+from\b.*",
    r"https?://\S+",
    r"\b[a-zA-Z][\w-]*\.[a-zA-Z]{2,}(/\S*)?\b",
    r"(^|\s)@[A-Za-z0-9_]{2,}",
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
        "telegram",
    ]
    if any(p in low for p in ad_patterns):
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


# ── Discord webhook helpers ───────────────────────────────────────────────────

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


def send_discord_webhook_text(text: str) -> list:
    ids = _send_webhook_text(DISCORD_WEBHOOK_URL, text)
    if DISCORD_WEBHOOK_URL:
        log("✅ Webhook Bee текст")
    return ids


def send_discord_webhook_photo(caption: str, image_bytes: bytes) -> list:
    ids = _send_webhook_photo(DISCORD_WEBHOOK_URL, caption, image_bytes)
    if DISCORD_WEBHOOK_URL:
        log("✅ Webhook Bee фото")
    return ids


# ── Telegram senders (боты) — каждый канал в своём try/except + свой токен ───

async def send_tg_text(context: ContextTypes.DEFAULT_TYPE, ru_text: str, en_text: str,
                       report: Optional[Report] = None, bundle: Optional[dict] = None):
    # TG #1 (Crypto Phoenix) — ЕДИНСТВЕННЫЙ таргет на русском
    if TARGET_CHAT_ID and ru_text:
        try:
            kwargs = dict(chat_id=TARGET_CHAT_ID, text=ru_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            if TARGET_MESSAGE_THREAD_ID:
                kwargs["message_thread_id"] = TARGET_MESSAGE_THREAD_ID
            m = await context.bot.send_message(**kwargs)
            bundle_add(bundle, "bot1", "TG #1", TARGET_CHAT_ID, m.message_id)
            log("✅ Sent text to TG #1 (RU)")
            if report: report.add("📱 Telegram", "TG #1", True)
        except Exception as e:
            log(f"❌ TG #1 error: {repr(e)}")
            if report: report.add("📱 Telegram", "TG #1", False, str(e)[:60])

    # TG #2 (Heaven) — английский оригинал
    if TARGET_CHAT_ID_2 and BOT_TOKEN_2 and en_text:
        try:
            bot2 = Bot(token=BOT_TOKEN_2)
            kwargs = dict(chat_id=TARGET_CHAT_ID_2, text=en_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            if TARGET_MESSAGE_THREAD_ID_2:
                kwargs["message_thread_id"] = TARGET_MESSAGE_THREAD_ID_2
            m = await bot2.send_message(**kwargs)
            bundle_add(bundle, "bot2", "Heaven", TARGET_CHAT_ID_2, m.message_id)
            log("✅ Sent text to TG #2 (Heaven, EN)")
            if report: report.add("📱 Telegram", "Heaven", True)
        except Exception as e:
            log(f"❌ TG #2 (Heaven) error: {repr(e)}")
            if report: report.add("📱 Telegram", "Heaven", False, str(e)[:60])


async def send_tg_photo_main(context: ContextTypes.DEFAULT_TYPE, file_id: str,
                             ru_caption: Optional[str],
                             report: Optional[Report] = None,
                             bundle: Optional[dict] = None):
    # TG #1 (Crypto Phoenix) — русская подпись, тот же бот → file_id валиден,
    # скачивание байтов не нужно: шлём сразу, не дожидаясь download_photo
    if not TARGET_CHAT_ID:
        return
    try:
        kwargs = dict(chat_id=TARGET_CHAT_ID, photo=file_id)
        if ru_caption:
            kwargs["caption"] = ru_caption
        if TARGET_MESSAGE_THREAD_ID:
            kwargs["message_thread_id"] = TARGET_MESSAGE_THREAD_ID
        m = await context.bot.send_photo(**kwargs)
        bundle_add(bundle, "bot1", "TG #1", TARGET_CHAT_ID, m.message_id)
        log("✅ Sent photo to TG #1 (RU)")
        if report: report.add("📱 Telegram", "TG #1", True)
    except Exception as e:
        log(f"❌ TG #1 photo error: {repr(e)}")
        if report: report.add("📱 Telegram", "TG #1", False, str(e)[:60])


async def send_tg_photo_heaven(en_caption: Optional[str], img_bytes: Optional[bytes],
                               report: Optional[Report] = None,
                               bundle: Optional[dict] = None):
    # TG #2 (Heaven) — английская подпись. ДРУГОЙ бот: file_id от бота #1
    # у него невалиден ('Wrong file identifier'), поэтому шлём фото байтами
    if not (TARGET_CHAT_ID_2 and BOT_TOKEN_2):
        return
    if not img_bytes:
        log("❌ TG #2 (Heaven): нет байтов фото — пропуск (file_id чужого бота слать нельзя)")
        if report: report.add("📱 Telegram", "Heaven", False, "фото не скачалось")
        return
    try:
        bot2 = Bot(token=BOT_TOKEN_2)
        kwargs = dict(chat_id=TARGET_CHAT_ID_2, photo=img_bytes)
        if en_caption:
            kwargs["caption"] = en_caption
        if TARGET_MESSAGE_THREAD_ID_2:
            kwargs["message_thread_id"] = TARGET_MESSAGE_THREAD_ID_2
        m = await bot2.send_photo(**kwargs)
        bundle_add(bundle, "bot2", "Heaven", TARGET_CHAT_ID_2, m.message_id)
        log("✅ Sent photo to TG #2 (Heaven, EN)")
        if report: report.add("📱 Telegram", "Heaven", True)
    except Exception as e:
        log(f"❌ TG #2 (Heaven) photo error: {repr(e)}")
        if report: report.add("📱 Telegram", "Heaven", False, str(e)[:60])


# ── Отправка от лица моего аккаунта (Telethon) ───────────────────────────────

def _parse_target_list(raw_chats: str, legacy_chat: str = "", legacy_topic: int = 0) -> list[tuple]:
    """
    Разбираем список чатов аккаунта в [(ref, topic_id), ...].
    raw_chats — через запятую, каждый элемент chat или chat:topic;
    legacy_chat/legacy_topic — старые одиночные переменные для совместимости.
    Дубликаты по ref отсеиваются.
    """
    raw_parts = []
    if raw_chats.strip():
        raw_parts.extend(raw_chats.split(","))
    if legacy_chat.strip():
        old = legacy_chat.strip()
        if legacy_topic:
            old += f":{legacy_topic}"
        raw_parts.append(old)

    result, seen = [], set()
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        topic = 0
        # topic указываем через ":", но у @username двоеточий не бывает,
        # а числовой id отрицательный — режем только последний ":digits"
        mt = re.match(r"^(.*?):(\d+)$", part)
        if mt:
            part, topic = mt.group(1).strip(), int(mt.group(2))

        if part.startswith("@"):
            ref = part
        else:
            try:
                ref = int(part)
            except ValueError:
                ref = part

        key = str(ref)
        if key in seen:
            continue
        seen.add(key)
        result.append((ref, topic))
    return result


def _account_configs() -> list[tuple]:
    """(имя, session-строка, [(ref, topic), ...]) — по одному на аккаунт."""
    return [
        (USER_LABEL,   TG_USER_SESSION,
         _parse_target_list(USER_TARGET_CHATS, USER_TARGET_CHAT, USER_TARGET_TOPIC_ID)),
        (USER_LABEL_2, TG_USER_SESSION_2,
         _parse_target_list(USER_TARGET_CHATS_2)),
    ]


def _configured_accounts() -> list[tuple]:
    """Только аккаунты, у которых заданы и сессия, и чаты."""
    return [(n, s, t) for n, s, t in _account_configs() if s and t]


async def _resolve_ref(client, ref):
    """
    Числовой id требует access_hash из кэша сессии; если его там нет —
    прогреваем кэш списком диалогов и пробуем снова.
    """
    try:
        return await client.get_entity(ref)
    except Exception as e:
        log(f"⚠️ Чат {ref} не нашёлся сразу ({repr(e)}), прогреваю кэш диалогов...")

    async for d in client.iter_dialogs():
        if d.id == ref or (isinstance(ref, str) and getattr(d.entity, "username", None) == ref.lstrip("@")):
            log(f"✅ Чат {ref} найден через список диалогов")
            return d.entity

    return await client.get_entity(ref)


async def user_init(app):
    """Поднимаем все аккаунты на том же event loop, что и бот, резолвим чаты."""
    global user_accounts
    configs = _configured_accounts()
    if not (TG_API_ID and TG_API_HASH and configs):
        log("⚪ Userbot: не настроен — пропуск")
        return
    for name, session, targets_cfg in configs:
        try:
            client = TelegramClient(StringSession(session), TG_API_ID, TG_API_HASH)
            client.parse_mode = None       # текст как есть, без markdown-разметки
            await client.connect()         # НЕ start(): иначе полезет спрашивать код в консоли
            if not await client.is_user_authorized():
                log(f"❌ Userbot {name}: сессия невалидна — перегенерируй session-строку")
                await client.disconnect()
                continue
            me = await client.get_me()

            resolved = []
            for ref, topic in targets_cfg:
                try:
                    entity = await _resolve_ref(client, ref)
                    title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(ref)
                    resolved.append((entity, topic, f"{title} ({name})"))
                    log(f"✅ Таргет {name} подключён: {title}" + (f" (topic {topic})" if topic else ""))
                except Exception as e:
                    log(f"❌ Таргет {name} {ref} не подключён: {repr(e)}")

            user_accounts.append({"name": name, "client": client, "targets": resolved})
            log(f"✅ Userbot {name} @{me.username or me.first_name}: активных чатов {len(resolved)}")
        except Exception as e:
            log(f"❌ Userbot {name} init error: {repr(e)}")


async def user_shutdown(app):
    for acc in user_accounts:
        try:
            await acc["client"].disconnect()
            log(f"👋 Userbot {acc['name']} отключён")
        except Exception as e:
            log(f"❌ Userbot {acc['name']} shutdown error: {repr(e)}")


async def _ensure_connected(acc) -> bool:
    """
    Telethon мог отвалиться (сеть моргнула, сессию убили) — тогда любая
    отправка падает с «Cannot send requests while disconnected». Перед
    отправкой проверяем связь и один раз пробуем переподключиться; если
    сессия недействительна — говорим об этом прямо, а не сыплем ошибками.
    """
    client = acc["client"]
    try:
        if not client.is_connected():
            log(f"🔌 Userbot {acc['name']}: соединение потеряно, переподключаюсь...")
            await client.connect()
        if not await client.is_user_authorized():
            log(f"❌ Userbot {acc['name']}: сессия недействительна — нужна новая "
                f"строка TG_USER_SESSION{'' if acc['name'] == USER_LABEL else '_2'}")
            return False
        return True
    except Exception as e:
        log(f"❌ Userbot {acc['name']}: переподключиться не удалось: {repr(e)}")
        return False


def _report_account_down(acc, report: Optional[Report], why: str):
    if report:
        for _, _, label in acc["targets"]:
            report.add("📱 Telegram", label, False, why)


async def send_user_text(text: str, report: Optional[Report] = None,
                         bundle: Optional[dict] = None):
    if not text:
        return
    for acc in user_accounts:
        if not await _ensure_connected(acc):
            _report_account_down(acc, report, "аккаунт отключён — см. лог")
            continue
        for entity, topic, label in acc["targets"]:
            try:
                kwargs = {}
                if topic:
                    kwargs["reply_to"] = topic
                m = await acc["client"].send_message(entity, text, link_preview=False, **kwargs)
                bundle_add(bundle, "user", label, acc["name"], entity, m.id)
                log(f"✅ Sent text to {label}")
                if report: report.add("📱 Telegram", label, True)
            except Exception as e:
                log(f"❌ {label} error: {repr(e)}")
                if report: report.add("📱 Telegram", label, False, str(e)[:60])


async def send_user_photo(img_bytes: Optional[bytes], caption: Optional[str],
                          report: Optional[Report] = None,
                          bundle: Optional[dict] = None):
    if not any(acc["targets"] for acc in user_accounts):
        return
    # аккаунт — тоже «чужой» клиент, file_id бота ему не подходит → только байты
    if not img_bytes:
        log("❌ Аккаунт-таргеты: нет байтов фото — пропуск")
        if report:
            for acc in user_accounts:
                for _, _, label in acc["targets"]:
                    report.add("📱 Telegram", label, False, "фото не скачалось")
        return
    for acc in user_accounts:
        if not await _ensure_connected(acc):
            _report_account_down(acc, report, "аккаунт отключён — см. лог")
            continue
        for entity, topic, label in acc["targets"]:
            try:
                bio = io.BytesIO(img_bytes)
                bio.name = "photo.jpg"     # Telethon берёт расширение из имени
                kwargs = {}
                if topic:
                    kwargs["reply_to"] = topic
                m = await acc["client"].send_file(entity, bio, caption=caption or "", **kwargs)
                bundle_add(bundle, "user", label, acc["name"], entity, m.id)
                log(f"✅ Sent photo to {label}")
                if report: report.add("📱 Telegram", label, True)
            except Exception as e:
                log(f"❌ {label} photo error: {repr(e)}")
                if report: report.add("📱 Telegram", label, False, str(e)[:60])


async def late_photo_wave(bot: Bot, file_id: str, dc_text: str,
                          report: Optional[Report] = None,
                          bundle: Optional[dict] = None):
    """
    Вторая волна: фото не скачалось с первого захода (Telegram лагал).
    Ждём, пробуем снова с длинной серией ретраев и догоняем все таргеты,
    которым нужны байты: Heaven, аккаунты, webhook Bee — а затем обычный
    отложенный конвейер (Rebel Angels + selfbot). TG #1 к этому моменту
    уже получил фото по file_id.
    """
    log(f"🔁 Вторая волна фото через {LATE_PHOTO_WAIT} сек...")
    await asyncio.sleep(LATE_PHOTO_WAIT)

    if bundle and bundle["deleted"]:
        log("🛑 Пост удалён через /posts — вторая волна отменена")
        return

    img_bytes = await download_photo(bot, file_id, attempts=5)

    # None оба сендера обрабатывают сами: лог + ❌ в сводке
    await send_tg_photo_heaven(dc_text, img_bytes, report, bundle)
    await send_user_photo(img_bytes, dc_text, report, bundle)

    if DISCORD_WEBHOOK_URL:
        try:
            if img_bytes:
                ids = send_discord_webhook_photo(dc_text, img_bytes)
                if report: report.add("🌐 Discord webhook", "Bee", True)
            elif dc_text:
                ids = send_discord_webhook_text(dc_text)
                if report: report.add("🌐 Discord webhook", "Bee", True)
            else:
                ids = []
            for mid in ids:
                bundle_add(bundle, "webhook", "Webhook Bee", DISCORD_WEBHOOK_URL, mid)
        except Exception as e:
            log(f"❌ Webhook Bee error: {repr(e)}")
            if report: report.add("🌐 Discord webhook", "Bee", False, str(e)[:60])

    if not dc_text and not img_bytes:
        log("⏭ Вторая волна: фото так и не скачалось, текста нет — стоп")
        if report:
            await send_report(bot, report)
        return

    # file_id передаём дальше: delayed_send сможет попробовать докачать
    # ещё раз после своей задержки 2-3 мин
    await delayed_send(dc_text, img_bytes, report, bot, file_id, bundle)


# ── Callout → CryptoTraders ───────────────────────────────────────────────────
# Независимая фоновая ветка: сигнал из источника (текст и/или карточка-картинка)
# распознаётся моделью и уходит selfbot'ом в формате callout-бота сервера
# CryptoTraders. Полностью выключена, пока не заданы CALLOUT_CHANNEL_ID и
# ANTHROPIC_API_KEY; на основную пересылку не влияет ни при каких ошибках.

CALLOUT_CHANNEL_ID = os.getenv("CALLOUT_CHANNEL_ID", "").strip()
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "").strip()
CALLOUT_MODEL      = os.getenv("CALLOUT_MODEL", "claude-haiku-4-5-20251001")

CALLOUT_PROMPT = """You convert crypto trading signals into commands for a Discord callout bot.

Input: a message from a trading channel — text and/or a screenshot of a position card.
Output: ONLY the command lines, nothing else. If the message is not an actionable
trading signal (balance updates, results recaps, chatter, ads), output exactly: SKIP

Command formats (pick one):
1) New trade:
Long BTC @ M 10X
TP: 105000, 106000
SL: 103000
   - side Long/Short, plain ticker; entry "@ M" if market/now, or "@ <price>"
   - leverage "NX" only if stated; omit TP/SL lines that are not given
2) Change stop or targets of an open trade: UPDATE BTC SL 25000  |  UPDATE BTC TP 30000, 35000
3) Average/DCA into an open trade: AVG BTC @ 30000   (or "AVG BTC" for market)
4) Partial close: PARTIAL CLOSE BTC @ M   (or "@ <price>")
5) Full close: CLOSE BTC @ M

Rules:
- Plain tickers only (BTC, ETH, VVV) — no $ signs, no /USDT, no exchange names.
- "k" means thousands: 76.7k -> 76700. Keep decimals exactly as given.
- "1st/2nd/3rd DCA <price>" or "EP" mentions = averaging (format 3) at that price.
- "Stops <price>" / "SL to <price>" for an open trade = UPDATE ... SL <price>.
- If text and image disagree, trust the more complete source; combine when they add up.
- Never invent numbers or coins that are not in the message. When unsure: SKIP
"""


def callout_enabled() -> bool:
    return bool(DISCORD_TOKEN and CALLOUT_CHANNEL_ID and ANTHROPIC_API_KEY)


def _callout_ask_model(text: str, img_bytes: Optional[bytes]) -> Optional[str]:
    """Синхронный вызов Anthropic API (гоняется через to_thread)."""
    import base64 as _b64
    content = []
    if img_bytes:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": _b64.b64encode(img_bytes).decode()},
        })
    content.append({"type": "text", "text": text or "(no text — read the card image)"})
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": CALLOUT_MODEL, "max_tokens": 300,
              "system": CALLOUT_PROMPT,
              "messages": [{"role": "user", "content": content}]},
        timeout=60,
    )
    r.raise_for_status()
    out = "".join(b.get("text", "") for b in r.json().get("content", [])
                  if b.get("type") == "text").strip()
    return out or None


async def callout_pipeline(bot: Bot, text: str, photo_file_id: Optional[str],
                           report: Optional[Report] = None,
                           bundle: Optional[dict] = None):
    try:
        img_bytes = None
        if photo_file_id:
            # качаем сами (с ретраями): ветка не должна зависеть от того,
            # успела ли скачать фото основная пересылка
            img_bytes = await download_photo(bot, photo_file_id)
        if not text and not img_bytes:
            return

        cmd = await asyncio.to_thread(_callout_ask_model, text, img_bytes)
        if not cmd or cmd.strip().upper().startswith("SKIP"):
            log("⏭ Callout: не сигнал — пропуск")
            return
        # страховка от разговорчивого ответа модели: команды всегда короткие
        if len(cmd) > 400 or cmd.count("\n") > 7:
            log(f"⏭ Callout: подозрительный ответ модели — пропуск: {cmd[:120]!r}")
            return

        if bundle and bundle["deleted"]:
            log("🛑 Пост удалён через /posts — callout отменён")
            return
        ok = await discord_send_text(cmd, CALLOUT_CHANNEL_ID)
        if isinstance(ok, str):
            bundle_add(bundle, "selfbot", "Callout CryptoTraders", CALLOUT_CHANNEL_ID, ok)
        if report:
            report.add("🤖 Selfbot", "Callout CryptoTraders", bool(ok))
        log(f"{'✅' if ok else '❌'} Callout → CryptoTraders:\n{cmd}")
    except Exception as e:
        log(f"❌ Callout error: {repr(e)}")


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

    reason = block_reason(raw_text)
    if reason:
        log(f"🚫 Пост отфильтрован: {reason}")
        await send_blocked_report(context.bot, raw_text, reason)
        return

    tg_text = transform_for_telegram(raw_text)
    dc_text = transform_for_discord(raw_text) or ""

    # Сводка о доставке — копится по ходу, уходит в самом конце.
    # В превью — английский оригинал (dc_text), русский только запасной вариант.
    preview = (dc_text or tg_text or "фото").splitlines()[0][:60] if (dc_text or tg_text) else "фото"
    report  = Report(preview)
    bundle  = new_bundle(preview)   # связка ID всех отправок — для /posts

    # 0. Callout для CryptoTraders — независимая фоновая ветка (стоп-фразы уже
    # отработали выше, так что реклама сюда не доходит)
    if callout_enabled():
        asyncio.create_task(callout_pipeline(
            context.bot, dc_text or raw_text,
            msg.photo[-1].file_id if msg.photo else None, report, bundle))

    # 1-2. Фото: TG #1 сразу по file_id (ему скачивание не нужно), потом
    # качаем байты для остальных. Не скачалось — вторую волну в фон и выходим:
    # она догонит Heaven, аккаунты, webhook Bee и отложенный конвейер.
    img_bytes = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
        await send_tg_photo_main(context, file_id, tg_text, report, bundle)
        img_bytes = await download_photo(context.bot, file_id)
        if img_bytes is None:
            asyncio.create_task(late_photo_wave(context.bot, file_id, dc_text, report, bundle))
            return
        await send_tg_photo_heaven(dc_text, img_bytes, report, bundle)
    elif tg_text or dc_text:
        await send_tg_text(context, tg_text, dc_text, report, bundle)
    else:
        log("⏭ Skip Telegram: empty")

    # 2b. Telegram — от лица моих аккаунтов. Английский оригинал
    if msg.photo:
        await send_user_photo(img_bytes, dc_text, report, bundle)
    elif dc_text:
        await send_user_text(dc_text, report, bundle)

    # 3. Discord webhook Bee (мгновенно)
    if DISCORD_WEBHOOK_URL:
        try:
            ids = []
            if img_bytes:
                ids = send_discord_webhook_photo(dc_text, img_bytes)
                report.add("🌐 Discord webhook", "Bee", True)
            elif dc_text:
                ids = send_discord_webhook_text(dc_text)
                report.add("🌐 Discord webhook", "Bee", True)
            for mid in ids:
                bundle_add(bundle, "webhook", "Webhook Bee", DISCORD_WEBHOOK_URL, mid)
        except Exception as e:
            log(f"❌ Webhook Bee error: {repr(e)}")
            report.add("🌐 Discord webhook", "Bee", False, str(e)[:60])

    # 4. Задержка → Rebel Angels webhook → selfbot каналы (в фоне),
    #    в самом конце — сводка о доставке. Если фото не скачалось,
    #    file_id даёт delayed_send шанс докачать его после задержки.
    photo_file_id = msg.photo[-1].file_id if msg.photo else None
    if not dc_text and not img_bytes and not photo_file_id:
        log("⏭ Skip delayed: empty")
        await send_report(context.bot, report)
        return

    asyncio.create_task(delayed_send(dc_text, img_bytes, report, context.bot, photo_file_id, bundle))


# ── Запуск ────────────────────────────────────────────────────────────────────

def main():
    validate_env()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(user_init)
        .post_shutdown(user_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start",         cmd_start))
    app.add_handler(CommandHandler("channels",      cmd_channels))
    app.add_handler(CommandHandler("addchannel",    cmd_addchannel))
    app.add_handler(CommandHandler("removechannel", cmd_removechannel))
    app.add_handler(CommandHandler("bridge",        cmd_bridge))
    app.add_handler(CommandHandler("status",        cmd_status))
    app.add_handler(CommandHandler("checkchats",    cmd_checkchats))
    app.add_handler(CommandHandler("mychats",       cmd_mychats))
    app.add_handler(CommandHandler("posts",         cmd_posts))
    app.add_handler(CallbackQueryHandler(cb_post_del_confirm, pattern=r"^pdelc:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_post_del_cancel,  pattern=r"^pdelx:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_post_del,         pattern=r"^pdel:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_ch_toggle,     pattern=r"^chtoggle:"))
    app.add_handler(CallbackQueryHandler(cb_bridge_toggle, pattern=r"^bridge_toggle$"))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))

    print("🚀 Signal filter bot started", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
