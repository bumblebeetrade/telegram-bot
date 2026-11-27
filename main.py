"""
Telegram → Discord Bridge

Источники: SOURCE_CHANNEL и (необязательно) SOURCE_CHANNEL_2 — два челленджа трейдера сразу
(старый доводит сделки, новый уже идёт). Посты обоих уходят во все таргеты; у webhook Bee для
второго может быть свой канал Discord (DISCORD_WEBHOOK_URL_SOURCE_2).

Языки таргетов:
  RU  — только TG #1 (Crypto Phoenix)
  EN  — все остальные: TG #2 (Heaven), TG #3 (канал, основной бот), аккаунты,
        Discord webhook Bee, Rebel Angels, selfbot каналы

Selfbot через 10 сек от прихода поста → каналы с паузами 5-7 сек →
webhook Rebel Angels → сводка в самом конце

Картинки проверяются как у бота 5k-50k (image_filter.py): дальше уходят только
карточки сделок и балансы; реклама, копитрейдинг, скрины Telegram/X/Discord — нет,
всё сомнительное — админу с кнопкой «Всё равно переслать».
Плюс таргеты от лица моих аккаунтов (Telethon)

Каждое направление — своя очередь: посты в нём идут по порядку, направления
друг друга не ждут (зависший аккаунт или вебхук не задерживает остальных).

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
import threading
import time
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
    ChatMemberHandler,
    ContextTypes,
    filters,
)

from telethon import TelegramClient
from telethon.sessions import StringSession

# фильтр текста и картинок — общий файл с ботом 5k-50k (там он filters.py)
from image_filter import cut_ad_tail, image_decision, qr_text, text_block_reason
import pings                     # тег роли Discord на важные действия трейдера (DISCORD_PING) — как у 5k-50k

BOT_TOKEN                  = os.getenv("BOT_TOKEN")
BOT_TOKEN_2                = os.getenv("BOT_TOKEN_2", "")  # Heaven — для TG #2

SOURCE_CHANNEL             = int(os.getenv("SOURCE_CHANNEL", "0"))
# Второй канал-источник: новый челлендж, пока в старом трейдер доводит сделки. Пусто — один источник.
SOURCE_CHANNEL_2           = int(os.getenv("SOURCE_CHANNEL_2", "0") or "0")
# подписи источников в сводках админу (например 250-1000 / 250-2500); в таргеты не уходят
SOURCE_LABEL               = os.getenv("SOURCE_LABEL", "").strip()
SOURCE_LABEL_2             = os.getenv("SOURCE_LABEL_2", "").strip()

TARGET_CHAT_ID             = int(os.getenv("TARGET_CHAT_ID", "0"))
TARGET_MESSAGE_THREAD_ID   = int(os.getenv("TARGET_MESSAGE_THREAD_ID", "0") or "0")

TARGET_CHAT_ID_2           = int(os.getenv("TARGET_CHAT_ID_2", "0"))
TARGET_MESSAGE_THREAD_ID_2 = int(os.getenv("TARGET_MESSAGE_THREAD_ID_2", "0") or "0")

# TG #3 — канал или группа на английском (без перевода), шлёт основной бот (BOT_TOKEN)
TARGET_CHAT_ID_3           = int(os.getenv("TARGET_CHAT_ID_3", "0") or "0")
TARGET_MESSAGE_THREAD_ID_3 = int(os.getenv("TARGET_MESSAGE_THREAD_ID_3", "0") or "0")
TARGET_LABEL_3             = os.getenv("TARGET_LABEL_3", "").strip() or "TG #3"

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
# webhook Bee для постов из SOURCE_CHANNEL_2 (свой канал Discord у нового челленджа);
# пусто — посты второго источника в webhook Bee не идут (в старый канал Discord не попадают)
DISCORD_WEBHOOK_URL_SOURCE_2 = os.getenv("DISCORD_WEBHOOK_URL_SOURCE_2", "")
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
# Selfbot-каналы и Rebel Angels: первый — через BRIDGE_DELAY сек от прихода поста,
# дальше паузы SEND_DELAY сек между каналами (чтобы Discord не счёл это спамом)
SEND_DELAY_MIN   = float(os.getenv("SEND_DELAY_MIN", "5") or "5")
SEND_DELAY_MAX   = float(os.getenv("SEND_DELAY_MAX", "7") or "7")
BRIDGE_DELAY_MIN = float(os.getenv("BRIDGE_DELAY_MIN", "10") or "10")
BRIDGE_DELAY_MAX = float(os.getenv("BRIDGE_DELAY_MAX", "10") or "10")

# Проверка картинок (OCR, как у бота 5k-50k):
#   strict (по умолчанию) — дальше уходят только карточки сделок/ордеров и балансы;
#          реклама и чужие скрины — блок, прочие картинки — админу на проверку;
#   ads   — блок только рекламы и чужих скринов, остальные картинки уходят;
#   off   — картинки не проверяются (как раньше).
IMAGE_FILTER = os.getenv("IMAGE_FILTER", "strict").strip().lower()
OCR_THREADS  = max(1, int(os.getenv("OCR_THREADS", "4") or "4"))
HELD_KEEP    = 20        # сколько отсеянных постов помнить для кнопки «Всё равно переслать»

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

# Отправки от лица аккаунтов (Telethon) ограничены по времени: 30.09 умершая сессия
# BumbleBee повесила отправку навсегда — и вместе с ней всю пересылку. Теперь зависший
# или умерший аккаунт даёт ❌ в сводке, а остальные таргеты получают пост как обычно.
USER_SEND_TIMEOUT     = int(os.getenv("USER_SEND_TIMEOUT", "30") or "30")
USER_PHOTO_TIMEOUT    = int(os.getenv("USER_PHOTO_TIMEOUT", "90") or "90")
# Railway при деплое поднимает новый контейнер, пока старый ещё работает. Если оба
# подключатся одной сессией с разных IP, Telegram навсегда её отзывает — поэтому аккаунты
# подключаются с паузой, когда старый контейнер уже отключился.
USER_START_DELAY      = int(os.getenv("USER_START_DELAY", "30") or "30")
# после этих ошибок сессия мертва навсегда — нужна новая строка
DEAD_SESSION_ERRORS   = {"AuthKeyUnregisteredError", "AuthKeyDuplicatedError", "SessionRevokedError",
                         "SessionExpiredError", "UserDeactivatedError", "UserDeactivatedBanError",
                         "AuthKeyInvalidError", "AuthKeyPermEmptyError"}


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


def _sources() -> dict:
    """Каналы-источники: id → подпись для сводок, webhook Bee и своя память сделок для тега роли
    (у двух челленджей свои сделки: «Lost» в одном не должен закрывать сделку другого)."""
    res = {SOURCE_CHANNEL: {"id": SOURCE_CHANNEL, "label": SOURCE_LABEL, "bee": DISCORD_WEBHOOK_URL,
                            "bee_label": "Bee", "bee_chain": "bee", "pinger": pings.Pinger()}}
    if SOURCE_CHANNEL_2 and SOURCE_CHANNEL_2 != SOURCE_CHANNEL:
        res[SOURCE_CHANNEL_2] = {
            "id": SOURCE_CHANNEL_2, "label": SOURCE_LABEL_2,
            # у нового челленджа — только свой webhook (свой канал Discord) и своя очередь; пока его нет,
            # в webhook Bee посты второго источника не идут (в старый канал Discord не попадают)
            "bee": DISCORD_WEBHOOK_URL_SOURCE_2,
            "bee_label": f"Bee {SOURCE_LABEL_2 or '#2'}",
            "bee_chain": "bee2",
            "pinger": pings.Pinger()}
    return res


SOURCES: dict = _sources()


def _src_tag(src: Optional[dict]) -> str:
    """« · 250-2500» — какой челлендж, для сводок админу (если источников два)."""
    return f" · {src['label']}" if src and src["label"] and len(SOURCES) > 1 else ""

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

    def __init__(self, preview: str = "", tag: str = ""):
        self.sections: dict[str, list[tuple[str, bool, str, str]]] = {}
        self.order: list[str] = []
        self.preview = preview
        self.tag = tag                  # « · 250-2500» — из какого челленджа пост
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
        head = f"{icon} <b>{html.escape(REPORT_TITLE)}</b>{html.escape(self.tag)} — доставлено {good}/{total}"

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


def _sec(a: float, b: float) -> str:
    return f"{a:g}" if a == b else f"{a:g}–{b:g}"


def _delay_text() -> str:
    return (f"{_sec(BRIDGE_DELAY_MIN, BRIDGE_DELAY_MAX)} сек от прихода поста, "
            f"между каналами {_sec(SEND_DELAY_MIN, SEND_DELAY_MAX)} сек")


async def send_blocked_report(bot: Bot, raw_text: str, reason: str, sent: str = "", tag: str = ""):
    """Уведомление о посте, который срезали фильтры. sent — сигнал без рекламного хвоста,
    который всё-таки ушёл (хвост вырезан). tag — « · 250-2500», из какого челленджа."""
    if not REPORT_ENABLED or not REPORT_BLOCKED:
        return
    flat = lambda s: " / ".join(line.strip() for line in s.splitlines() if line.strip())[:200]
    preview = flat(raw_text) or "(без текста)"
    head = "✂️ <b>Вырезан рекламный хвост — сигнал ушёл</b>" if sent else "🚫 <b>Пост отфильтрован</b>"
    text = (
        f"{head}{html.escape(tag)} · {_hm()}\n"
        f"<i>{html.escape(preview)}</i>\n\n"
        f"Причина: <code>{html.escape(reason)}</code>"
        + (f"\nУшло: <i>{html.escape(flat(sent))}</i>" if sent else "")
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
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
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


# ── Проверка картинок ────────────────────────────────────────────────────────

_ocr = None
_ocr_lock = threading.Lock()          # один OCR за раз
def _ocr_load():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        # потоков — не больше, чем даёт Railway: лишние только мешают друг другу
        _ocr = RapidOCR(intra_op_num_threads=OCR_THREADS, inter_op_num_threads=1)
        log(f"🔤 OCR загружен (потоков: {OCR_THREADS})")


def _ocr_warmup():
    """Загрузить OCR сразу при старте — чтобы первая карточка не ждала загрузки модели."""
    try:
        with _ocr_lock:
            _ocr_load()
    except Exception as e:
        log(f"⚠️ OCR не загрузился при старте: {repr(e)} — попробую на первой картинке")


def _ocr_lines(data: bytes) -> list:
    with _ocr_lock:
        _ocr_load()
        res, _ = _ocr(data)
    return [r[1] for r in (res or [])]


async def check_image(data: bytes) -> tuple:
    """(решение, пояснение): pass — карточка сделки, уходит; block — реклама / чужой скрин;
    review — не похоже на карточку или не удалось проверить: решает админ."""
    if IMAGE_FILTER == "off":
        return "pass", "проверка картинок выключена"
    t0 = time.time()
    try:
        lines = await asyncio.to_thread(_ocr_lines, data)
    except Exception as e:
        log(f"⚠️ OCR ошибка: {repr(e)}")
        return "review", "картинку не удалось проверить (OCR)"
    qr = await asyncio.to_thread(qr_text, data)
    # решение — общее с ботом 5k-50k (image_filter.image_decision): strict — только карточки, балансы,
    # графики и сигнал на картинке; ads — всё, где нет рекламы
    status, why = image_decision(lines, qr, strict=IMAGE_FILTER == "strict")
    log(f"🔤 {' | '.join(lines)[:160]!r}{' + QR' if qr else ''} → "
        f"{ {'pass': '✅', 'block': '⛔', 'review': '✋'}[status] } {why} ({time.time() - t0:.1f} с)")
    return status, why


async def _photo_verdict(img_task: asyncio.Task) -> tuple:
    """(решение, пояснение) для фото поста — после скачивания (со второй волной)."""
    img = await img_task
    if img is None:
        return "review", "фото не скачалось — не проверено"
    return await check_image(img)


async def _if_pass(verdict_task: Optional[asyncio.Task], make):
    """Отправка в направление — только если картинка прошла проверку (у текста проверки нет)."""
    if verdict_task is not None and (await verdict_task)[0] != "pass":
        return None
    return await make()


# Отсеянные посты с картинкой — для кнопки «Всё равно переслать»
held_posts: deque = deque(maxlen=HELD_KEEP)
_held_seq: int = 0
_held_done: dict = {}         # uid → что с ним сделали (кнопку могли нажать несколько админов)


async def _report_held(bot: Bot, status: str, why: str, post: dict):
    """Пост с картинкой не ушёл: админу — картинка, причина и кнопки."""
    global _held_seq
    _held_seq += 1
    post = dict(post, uid=_held_seq, why=why)
    held_posts.append(post)
    head = ("⛔ <b>Не переслано — картинка</b>" if status == "block" else
            "✋ <b>Нужна ваша проверка — картинка</b>")
    tag = html.escape(_src_tag(SOURCES.get(post.get("src"))))
    caption = (f"{head}{tag} · {_hm()}\n{html.escape(why)}\n\n"
               f"<i>{html.escape((post['dc_text'] or post['tg_text'] or '(без текста)')[:600])}</i>")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Всё равно переслать", callback_data=f"hforce:{_held_seq}"),
                                InlineKeyboardButton("🗑 Убрать", callback_data=f"hdrop:{_held_seq}")]])
    log(f"{'⛔' if status == 'block' else '✋'} пост с картинкой не переслан: {why}")
    if status == "block" and not (REPORT_ENABLED and REPORT_BLOCKED):
        return
    for chat_id in _report_targets():
        try:
            await bot.send_photo(chat_id=chat_id, photo=post["file_id"], caption=caption[:1024],
                                 parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:
            log(f"❌ Отсеянный пост → {chat_id}: {repr(e)}")


async def cb_held_force(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Нет доступа.")
        return
    uid = int(query.data.split(":")[1])
    post = next((p for p in held_posts if p["uid"] == uid), None)
    if post is None:
        await query.answer(f"Уже {_held_done[uid]}." if uid in _held_done else
                           "Этого поста уже нет в памяти (бот перезапускался).", show_alert=True)
        return
    held_posts.remove(post)
    _held_done[uid] = "переслано"
    await query.answer("Пересылаю во все таргеты…")
    log(f"✅ Админ разрешил пост с картинкой ({post['why']}) — пересылаю")
    await _dispatch(ctx, post["tg_text"], post["dc_text"], post["file_id"], force=True,
                    src=SOURCES.get(post.get("src")))
    try:
        await query.edit_message_caption(caption=f"✅ Переслано вручную · {_hm()}\n{html.escape(post['why'])}",
                                         parse_mode=ParseMode.HTML)
    except Exception:
        pass


async def cb_held_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Нет доступа.")
        return
    uid = int(query.data.split(":")[1])
    post = next((p for p in held_posts if p["uid"] == uid), None)
    if post is None:
        await query.answer(f"Уже {_held_done[uid]}." if uid in _held_done else "Уже убрано.")
        return
    held_posts.remove(post)
    _held_done[uid] = "убрано"
    await query.answer("Убрано")
    try:
        await query.edit_message_caption(caption=f"🗑 Не пересылаем · {_hm()}", parse_mode=ParseMode.HTML)
    except Exception:
        pass


# ── Конвейер поста ────────────────────────────────────────────────────────────
#
# Каждое направление — TG #1, Heaven, webhook Bee, каждый аккаунт, каждый selfbot-канал,
# Rebel Angels — своя очередь: в неё посты уходят строго по порядку (карточка не обгонит
# текст), но направления друг друга не ждут: зависший аккаунт, упавший вебхук или медленный
# Discord не задерживают остальных. Задержка selfbot-каналов и Rebel Angels считается
# с момента, когда пост пришёл из канала-источника.

_chains: dict = {}
_last_deadline = 0.0          # задержки selfbot идут по порядку постов: следующий не раньше предыдущего
REPORT_MAX_WAIT = 20 * 60     # сводка ждёт отправки не дольше (каждая и так ограничена таймаутами)


def _chain(name: str, make) -> asyncio.Task:
    """Запустить make() после предыдущей отправки в то же направление; остальных не ждать."""
    prev = _chains.get(name)

    async def run():
        if prev is not None and not prev.done():
            await asyncio.wait({prev})          # ошибка или зависание соседа нас не касаются
        return await make()
    task = asyncio.create_task(run())
    _chains[name] = task
    return task


async def delayed_send(text: str, img_task: Optional[asyncio.Task], report: Report, bot: Optional[Bot],
                       photo_file_id: Optional[str], bundle: Optional[dict], arrived: float,
                       ping: Optional[str] = None) -> list:
    """Задержка BRIDGE_DELAY (10 сек) от прихода поста → selfbot-каналы (паузы SEND_DELAY,
    5-7 сек) → пауза → Rebel Angels. Ставит отправки в очереди каналов и возвращает их —
    сводка их дождётся. ping — тег роли (pings.py): в каналы из DISCORD_PING текст уходит
    с тегом последней строкой."""
    global _last_deadline

    def cancelled() -> bool:
        return bool(bundle and bundle["deleted"])

    if not bridge_enabled:
        report.add("🤖 Selfbot", "автопересылка выключена", False, "/bridge")
        return []

    delay = random.uniform(BRIDGE_DELAY_MIN, BRIDGE_DELAY_MAX)
    deadline = max(arrived + delay, _last_deadline + 1)
    _last_deadline = deadline
    wait = deadline - time.time()
    log(f"⏳ Selfbot и Rebel Angels — через {max(wait, 0):.0f} сек (задержка {delay:.0f} сек от прихода поста)")
    if wait > 0:
        await asyncio.sleep(wait)

    if not bridge_enabled:
        report.add("🤖 Selfbot", "автопересылка выключена", False, "/bridge")
        return []

    img_bytes = await img_task if img_task is not None else None
    # фото могло не скачаться (Telegram таймаутил) — пробуем ещё раз, обычно API уже отвечает
    if img_bytes is None and photo_file_id and bot:
        log("🔁 Повторная попытка скачать фото для отложенной отправки...")
        img_bytes = await download_photo(bot, photo_file_id)

    if not text and not img_bytes:
        log("⏭ Delayed: фото так и не скачалось, текста нет — пропуск")
        return []
    if cancelled():
        log("🛑 Пост удалён через /posts — отложенная отправка отменена")
        return []

    # отправки ставим в очереди каналов сразу; внутри поста — паузы 7-10 сек между каналами
    tasks, at = [], time.time()
    if DISCORD_TOKEN:
        targets = [(n, all_channels[n]) for n in active_channels if n in all_channels]
        if not targets:
            log("⏭ Selfbot: нет активных каналов")
        for i, (name, channel_id) in enumerate(targets):
            if i > 0:
                at += random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
            tasks.append(_chain(f"selfbot:{channel_id}",
                                lambda name=name, channel_id=channel_id, at=at:
                                _selfbot_at(at, name, channel_id, text, img_bytes, report, bundle, ping)))
    if DISCORD_WEBHOOK_URL_2:
        at += random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
        tasks.append(_chain("rebel", lambda at=at: _rebel_at(at, text, img_bytes, report, bundle)))
    return tasks


async def _selfbot_at(at: float, name: str, channel_id: str, text: str, img_bytes: Optional[bytes],
                      report: Report, bundle: Optional[dict], ping: Optional[str] = None):
    if at > time.time():
        await asyncio.sleep(at - time.time())
    if bundle and bundle["deleted"]:
        log(f"🛑 Пост удалён через /posts — {name} пропущен")
        return
    role = pings.role_for(name, channel_id) if ping else None
    if role:
        text = pings.with_tag(text, role)
        log(f"🔔 {name}: тег роли ({ping})")
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
    report.add("🤖 Selfbot", f"{name} 🔔" if role else name, bool(ok))


async def _rebel_at(at: float, text: str, img_bytes: Optional[bytes], report: Report, bundle: Optional[dict]):
    if at > time.time():
        await asyncio.sleep(at - time.time())
    if bundle and bundle["deleted"]:
        return
    try:
        ids = []
        if img_bytes:
            ids = await asyncio.to_thread(_send_webhook_photo, DISCORD_WEBHOOK_URL_2, text, img_bytes)
            log("✅ Webhook Rebel Angels фото")
        elif text:
            ids = await asyncio.to_thread(_send_webhook_text, DISCORD_WEBHOOK_URL_2, text)
            log("✅ Webhook Rebel Angels текст")
        for mid in ids:
            bundle_add(bundle, "webhook", "Webhook Rebel Angels", DISCORD_WEBHOOK_URL_2, mid)
        report.add("🌐 Discord webhook", "Rebel Angels", True)
    except Exception as e:
        log(f"❌ Webhook Rebel Angels error: {repr(e)}")
        report.add("🌐 Discord webhook", "Rebel Angels", False, str(e)[:60])


async def _report_when_done(bot: Bot, report: Report, bundle: Optional[dict], tasks: list, launchers: list,
                            verdict_task: Optional[asyncio.Task] = None, post: Optional[dict] = None):
    """Сводка — одна, когда закончились все отправки поста (каждая ограничена таймаутами).
    Картинка не прошла проверку — вместо сводки админу картинка, причина и кнопки."""
    if verdict_task is not None:
        status, why = await verdict_task
        if status != "pass":
            if bundle in sent_posts:
                sent_posts.remove(bundle)              # ничего не отправлено — в /posts ему не место
            await _report_held(bot, status, why, post)
            return
    until = time.time() + REPORT_MAX_WAIT
    pending = [t for t in tasks if t is not None]
    for launcher in launchers:
        if launcher is None:
            continue
        try:
            pending += list(await asyncio.wait_for(asyncio.shield(launcher), max(1.0, until - time.time())) or [])
        except Exception as e:
            log(f"⚠️ Сводка: часть отправок не запустилась: {repr(e)}")
    if pending:
        _, left = await asyncio.wait(pending, timeout=max(1.0, until - time.time()))
        if left:
            log(f"⚠️ Сводка: {len(left)} отправок не закончились за {REPORT_MAX_WAIT // 60} мин — шлю как есть")
    if bundle and bundle["deleted"]:
        return
    await send_report(bot, report)


# ── Команды ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    status = "🟢 Вкл" if bridge_enabled else "🔴 Выкл"
    sources = ", ".join(html.escape(s["label"] or str(cid)) for cid, s in SOURCES.items())
    await update.message.reply_text(
        f"📡 <b>Signal Bot</b>\n\n"
        f"Источники: {sources}\n"
        f"Автопересылка: {status}\n"
        f"Активных каналов: {len(active_channels)} из {len(all_channels)}\n"
        f"Задержка: {_delay_text()}\n\n"
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

    # каналы-источники: бот должен быть в них админом — иначе Telegram не присылает ему посты
    for cid, src in SOURCES.items():
        name = f"Источник {src['label']}" if src["label"] else "Источник"
        try:
            chat = await ctx.bot.get_chat(cid)
            results.append(f"✅ {html.escape(name)}: <code>{cid}</code> — {html.escape(chat.title or '?')}")
        except Exception as e:
            results.append(f"❌ {html.escape(name)}: <code>{cid}</code> — {html.escape(repr(e))} "
                           "(бот должен быть админом канала)")

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

    # TG #3 — основной бот, английский
    if TARGET_CHAT_ID_3:
        name = html.escape(TARGET_LABEL_3)
        try:
            chat = await ctx.bot.get_chat(TARGET_CHAT_ID_3)
            me = await ctx.bot.get_chat_member(TARGET_CHAT_ID_3, ctx.bot.id)
            can = me.status == "creator" or (me.status == "administrator" and
                                             (chat.type != "channel" or getattr(me, "can_post_messages", False)))
            can = can or (chat.type != "channel" and me.status == "member")
            results.append(f"{'✅' if can else '❌'} {name}: <code>{TARGET_CHAT_ID_3}</code>"
                           f"{f' (thread {TARGET_MESSAGE_THREAD_ID_3})' if TARGET_MESSAGE_THREAD_ID_3 else ''}"
                           f" — {html.escape(chat.title or '?')}"
                           + ("" if can else " — у бота нет права публиковать (сделайте админом)"))
        except Exception as e:
            results.append(f"❌ {name}: <code>{TARGET_CHAT_ID_3}</code> — {html.escape(repr(e))}")

    # Аккаунт-таргеты (Telethon) — все подключённые аккаунты
    if user_accounts:
        for acc in user_accounts:
            if acc.get("dead"):
                results.append(f"💀 Аккаунт {html.escape(acc['name'])}: сессия умерла — нужна новая "
                               f"<code>{_session_var(acc['name'])}</code>")
                continue
            try:
                me = await asyncio.wait_for(acc["client"].get_me(), USER_SEND_TIMEOUT)
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

        if acc.get("dead"):
            await update.message.reply_text(f"💀 {acc['name']}: сессия умерла — нужна новая {_session_var(acc['name'])}")
            continue
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
        f"🌉 <b>Автопересылка в Discord</b>\n\nСтатус: {label}\nЗадержка: {_delay_text()}",
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
        f"🌉 <b>Автопересылка в Discord</b>\n\nСтатус: {label}\nЗадержка: {_delay_text()}",
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
                    + (" 🔔 тег роли на сделки" if pings.role_for(n, cid) else "")
                    for n, cid in all_channels.items()
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
                    f"Задержка: {_delay_text()}\n\n"
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
    (вторая волна фото, delayed_send и отправки в selfbot-каналы проверяют его).
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
    """Причина, по которой пост режется целиком. None — пост проходит.
    Правила — общие с ботом 5k-50k (image_filter.py = filters.py там): стоп-лист Arki, продажа
    мест, призывы к подписчикам (лайки, follow, вступить, оплата, уведомления), бонусы и UID
    бирж, розыгрыши, спонсоры, ссылки и @username — в том числе замаскированные."""
    return text_block_reason(raw_text)


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


users_ready = asyncio.Event()        # аккаунты подключены (или подключать нечего)
APP_BOT: Optional[Bot] = None        # бот пульта — для тревог об аккаунтах


def _session_var(name: str) -> str:
    return "TG_USER_SESSION" if name == USER_LABEL else "TG_USER_SESSION_2"


async def _alert(text: str):
    if APP_BOT is not None:
        await _deliver_report(APP_BOT, text)


async def user_init(app):
    """Аккаунты подключаем в фоне с паузой USER_START_DELAY — бот при этом уже работает."""
    global APP_BOT
    APP_BOT = app.bot
    if pings.CONFIG:
        tagged = [n for n, cid in all_channels.items() if pings.role_for(n, cid)]
        log(f"🔔 Тег роли (DISCORD_PING): {', '.join(tagged) or 'ни одного канала'}")
        lost = pings.unmatched(all_channels.items())
        if lost:
            log(f"⚠️ DISCORD_PING: нет канала «{', '.join(lost)}» в DISCORD_CHANNELS")
            asyncio.create_task(_alert(
                f"⚠️ <b>DISCORD_PING</b>: не нашёл канал «{html.escape(', '.join(lost))}» в DISCORD_CHANNELS — "
                "там тега не будет. Слева в DISCORD_PING — название канала как в DISCORD_CHANNELS или id канала."))
    asyncio.create_task(_user_init_later())
    if IMAGE_FILTER != "off":
        asyncio.create_task(asyncio.to_thread(_ocr_warmup))


async def _user_init_later():
    try:
        if _configured_accounts() and TG_API_ID and TG_API_HASH and USER_START_DELAY > 0:
            log(f"⏳ Userbot: подключу аккаунты через {USER_START_DELAY} сек "
                "(старый контейнер Railway должен успеть отключиться)")
            await asyncio.sleep(USER_START_DELAY)
        await _user_init_now()
    finally:
        users_ready.set()


async def _user_init_now():
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
            await asyncio.wait_for(client.connect(), USER_SEND_TIMEOUT)   # НЕ start(): спросит код в консоли
            if not await asyncio.wait_for(client.is_user_authorized(), USER_SEND_TIMEOUT):
                log(f"❌ Userbot {name}: сессия невалидна — нужна новая строка {_session_var(name)}")
                await _alert(f"❌ <b>Аккаунт {html.escape(name)}</b>: сессия недействительна — его таргеты "
                             f"не получают посты. Нужна новая строка <code>{_session_var(name)}</code> "
                             "(python make_session.py" + ("" if name == USER_LABEL else " ivan") + ").")
                await client.disconnect()
                continue
            me = await asyncio.wait_for(client.get_me(), USER_SEND_TIMEOUT)

            resolved = []
            for ref, topic in targets_cfg:
                try:
                    entity = await asyncio.wait_for(_resolve_ref(client, ref), 60)
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


async def _mark_dead(acc, err: Exception):
    """Сессия аккаунта умерла навсегда: больше не пытаемся, один раз сообщаем, что делать."""
    if acc.get("dead"):
        return
    acc["dead"] = type(err).__name__
    log(f"💀 Userbot {acc['name']}: сессия умерла ({type(err).__name__}) — его таргеты пропускаются, "
        f"нужна новая строка {_session_var(acc['name'])}")
    await _alert(f"💀 <b>Аккаунт {html.escape(acc['name'])}</b>: Telegram отозвал сессию "
                 f"(<code>{html.escape(type(err).__name__)}</code>). Его таргеты "
                 f"({', '.join(html.escape(l) for _, _, l in acc['targets'])}) пропускаются, "
                 "остальные получают посты как обычно.\n\nЧто сделать: <code>python make_session.py"
                 + ("" if acc["name"] == USER_LABEL else " ivan") + f"</code> → новая строка в "
                 f"<code>{_session_var(acc['name'])}</code> (Railway → Variables).")


async def _ensure_connected(acc) -> bool:
    """
    Telethon мог отвалиться (сеть моргнула, сессию убили) — тогда любая
    отправка падает с «Cannot send requests while disconnected». Перед
    отправкой проверяем связь и один раз пробуем переподключиться; после
    переподключения проверяем сессию настоящим запросом (is_user_authorized
    Telethon помнит старый ответ и мёртвую сессию не замечает).
    """
    if acc.get("dead"):
        return False
    client = acc["client"]
    try:
        if acc.pop("stale", False) and client.is_connected():
            await asyncio.wait_for(client.disconnect(), 15)   # прошлая отправка зависла
        if not client.is_connected():
            log(f"🔌 Userbot {acc['name']}: соединение потеряно, переподключаюсь...")
            await asyncio.wait_for(client.connect(), USER_SEND_TIMEOUT)
            await asyncio.wait_for(client.get_me(), USER_SEND_TIMEOUT)
        return True
    except Exception as e:
        if type(e).__name__ in DEAD_SESSION_ERRORS:
            await _mark_dead(acc, e)
        else:
            log(f"❌ Userbot {acc['name']}: переподключиться не удалось: {repr(e)}")
        return False


def _report_account_down(acc, report: Optional[Report], why: str):
    if report:
        for _, _, label in acc["targets"]:
            report.add("📱 Telegram", label, False, why)


def _down_reason(acc) -> str:
    if acc.get("dead"):
        return f"сессия умерла — нужна новая {_session_var(acc['name'])}"
    return "аккаунт не на связи — см. лог"


async def _user_send(acc, targets: list, send_one, timeout: int, kind: str,
                     report: Optional[Report], bundle: Optional[dict]):
    """Отправка по таргетам аккаунта: у каждой — таймаут; мёртвая сессия или зависание —
    остальные таргеты этого аккаунта пропускаем (они повисли бы так же), идём дальше."""
    for i, (entity, topic, label) in enumerate(targets):
        try:
            m = await asyncio.wait_for(send_one(entity, topic), timeout)
            bundle_add(bundle, "user", label, acc["name"], entity, m.id)
            log(f"✅ Sent {kind} to {label}")
            if report: report.add("📱 Telegram", label, True)
        except asyncio.TimeoutError:
            acc["stale"] = True
            log(f"❌ {label}: аккаунт не ответил за {timeout} сек — пропускаю его таргеты")
            if report:
                report.add("📱 Telegram", label, False, f"аккаунт не ответил за {timeout} сек")
                for _, _, rest in targets[i + 1:]:
                    report.add("📱 Telegram", rest, False, "пропущен: аккаунт завис")
            return
        except Exception as e:
            if type(e).__name__ in DEAD_SESSION_ERRORS:
                await _mark_dead(acc, e)
                if report:
                    for _, _, rest in targets[i:]:
                        report.add("📱 Telegram", rest, False, _down_reason(acc))
                return
            log(f"❌ {label} {kind} error: {repr(e)}")
            if report: report.add("📱 Telegram", label, False, str(e)[:60])


async def _wait_users_ready():
    try:
        await asyncio.wait_for(users_ready.wait(), USER_START_DELAY + 120)
    except asyncio.TimeoutError:
        pass


async def _launch_users(text: str, is_photo: bool, img_task: Optional[asyncio.Task],
                        report: Report, bundle: Optional[dict]) -> list:
    """По очереди на аккаунт: аккаунты друг друга не ждут, в каждом — посты по порядку."""
    await _wait_users_ready()
    return [_chain(f"user:{acc['name']}",
                   lambda acc=acc: _user_account(acc, text, is_photo, img_task, report, bundle))
            for acc in user_accounts if acc["targets"]]


async def _user_account(acc, text: str, is_photo: bool, img_task: Optional[asyncio.Task],
                        report: Report, bundle: Optional[dict]):
    img = None
    if is_photo:
        img = await img_task
        if not img:
            # аккаунт — тоже «чужой» клиент, file_id бота ему не подходит → только байты
            log(f"❌ Аккаунт {acc['name']}: нет байтов фото — пропуск")
            for _, _, label in acc["targets"]:
                report.add("📱 Telegram", label, False, "фото не скачалось")
            return
    elif not text:
        return
    if not await _ensure_connected(acc):
        _report_account_down(acc, report, _down_reason(acc))
        return
    client = acc["client"]
    if img:
        def send_one(entity, topic):
            bio = io.BytesIO(img)
            bio.name = "photo.jpg"     # Telethon берёт расширение из имени
            return client.send_file(entity, bio, caption=text or "", **({"reply_to": topic} if topic else {}))
        await _user_send(acc, acc["targets"], send_one, USER_PHOTO_TIMEOUT, "photo", report, bundle)
    else:
        def send_one(entity, topic):
            return client.send_message(entity, text, link_preview=False, **({"reply_to": topic} if topic else {}))
        await _user_send(acc, acc["targets"], send_one, USER_SEND_TIMEOUT, "text", report, bundle)


async def _photo_bytes(bot: Bot, file_id: str, bundle: Optional[dict] = None) -> Optional[bytes]:
    """Байты фото — для всех, кроме TG #1 (ему хватает file_id). Не скачалось сразу
    (Telegram лагал) — вторая волна через LATE_PHOTO_WAIT сек с длинной серией попыток."""
    img = await download_photo(bot, file_id)
    if img is None and not (bundle and bundle["deleted"]):
        log(f"🔁 Вторая волна фото через {LATE_PHOTO_WAIT} сек...")
        await asyncio.sleep(LATE_PHOTO_WAIT)
        if not (bundle and bundle["deleted"]):
            img = await download_photo(bot, file_id, attempts=5)
    return img


async def _send_heaven(context, is_photo: bool, dc_text: str, img_task: Optional[asyncio.Task],
                       report: Report, bundle: Optional[dict]):
    if is_photo:
        # None send_tg_photo_heaven обрабатывает сам: лог + ❌ в сводке
        await send_tg_photo_heaven(dc_text, await img_task, report, bundle)
    elif dc_text:
        await send_tg_text(context, "", dc_text, report, bundle)      # только TG #2 (Heaven)


async def _send_webhook_bee(dc_text: str, img_task: Optional[asyncio.Task], report: Report,
                            bundle: Optional[dict], src: dict):
    """Webhook Bee — у каждого источника свой (DISCORD_WEBHOOK_URL / DISCORD_WEBHOOK_URL_SOURCE_2)."""
    url, label = src["bee"], src["bee_label"]
    if not url:
        return
    img = await img_task if img_task is not None else None
    try:
        ids = []
        if img:
            ids = await asyncio.to_thread(_send_webhook_photo, url, dc_text, img)
            log(f"✅ Webhook {label} фото")
            report.add("🌐 Discord webhook", label, True)
        elif dc_text:
            ids = await asyncio.to_thread(_send_webhook_text, url, dc_text)
            log(f"✅ Webhook {label} текст")
            report.add("🌐 Discord webhook", label, True)
        for mid in ids:
            bundle_add(bundle, "webhook", f"Webhook {label}", url, mid)
    except Exception as e:
        log(f"❌ Webhook {label} error: {repr(e)}")
        report.add("🌐 Discord webhook", label, False, str(e)[:60])


async def _send_tg3(context, is_photo: bool, file_id: Optional[str], en_text: str,
                    report: Report, bundle: Optional[dict]):
    """TG #3 — английский оригинал без перевода. Шлёт основной бот: file_id фото у него свой,
    поэтому карточка уходит сразу после проверки, без скачивания. Текст — как есть, без разметки."""
    if not TARGET_CHAT_ID_3:
        return
    kw = {"message_thread_id": TARGET_MESSAGE_THREAD_ID_3} if TARGET_MESSAGE_THREAD_ID_3 else {}
    try:
        if is_photo:
            m = await context.bot.send_photo(chat_id=TARGET_CHAT_ID_3, photo=file_id,
                                             caption=(en_text or None) and en_text[:1024], **kw)
        elif en_text:
            m = await context.bot.send_message(chat_id=TARGET_CHAT_ID_3, text=en_text,
                                               disable_web_page_preview=True, **kw)
        else:
            return
        bundle_add(bundle, "bot1", TARGET_LABEL_3, TARGET_CHAT_ID_3, m.message_id)
        log(f"✅ Sent {'photo' if is_photo else 'text'} to {TARGET_LABEL_3} (EN)")
        report.add("📱 Telegram", TARGET_LABEL_3, True)
    except Exception as e:
        log(f"❌ {TARGET_LABEL_3} error: {repr(e)}")
        report.add("📱 Telegram", TARGET_LABEL_3, False, str(e)[:60])


# ── Handler ───────────────────────────────────────────────────────────────────

post_lock = asyncio.Lock()   # посты — строго по одному и по порядку; команды при этом отвечают

# Тег роли Discord (pings.py, переменная DISCORD_PING): решение — по каждому посту, по порядку;
# память сделок у каждого источника своя (SOURCES[...]["pinger"])
PINGER = SOURCES[SOURCE_CHANNEL]["pinger"]


def _ping_decide(text: str, src: Optional[dict] = None) -> Optional[str]:
    """Ставить ли тег на этот пост («вход», «закрытие», …) или None. Сбой правила пересылку не трогает."""
    if not pings.CONFIG or not text:
        return None
    try:
        kind = (src or SOURCES[SOURCE_CHANNEL])["pinger"].decide(text)
    except Exception as e:
        log(f"⚠️ Тег: сбой правила ({repr(e)}) — пост уходит без тега")
        return None
    if kind:
        log(f"🔔 тег роли: {kind}")
    return kind


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with post_lock:
        await _handle_channel_post(update, context)


async def _handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.channel_post
    if not msg:
        return
    src = SOURCES.get(msg.chat.id)
    if src is None:
        log(f"⏭ Skip: wrong channel ({msg.chat.id})")
        return

    raw_text = msg.text or msg.caption or ""
    tag = _src_tag(src)
    log(f"\n{'='*50}\n📨 Incoming{tag}:\n{raw_text[:400]}\n{'='*50}")

    reason = block_reason(raw_text)
    if reason:
        # «Long $btc / Stops 61k / 100 likes please ♥️» — сигнал сверху уходит, хвост режется
        head = cut_ad_tail(raw_text)
        if not head:
            log(f"🚫 Пост отфильтрован: {reason}")
            await send_blocked_report(context.bot, raw_text, reason, tag=tag)
            return
        log(f"✂️ Вырезан рекламный хвост ({reason}) — сигнал уходит")
        await send_blocked_report(context.bot, raw_text, reason, sent=head, tag=tag)
        raw_text = head

    tg_text = transform_for_telegram(raw_text)
    dc_text = transform_for_discord(raw_text) or ""
    file_id = msg.photo[-1].file_id if msg.photo else None
    await _dispatch(context, tg_text, dc_text, file_id, src=src)


async def _dispatch(context, tg_text: str, dc_text: str, file_id: Optional[str], force: bool = False,
                    src: Optional[dict] = None):
    """Рассылка поста по всем направлениям. force — админ разрешил картинку вручную.
    src — канал-источник (SOURCES): от него зависят webhook Bee и память сделок для тега."""
    src = src or SOURCES[SOURCE_CHANNEL]
    # Сводка о доставке — копится по ходу, уходит в самом конце.
    # В превью — английский оригинал (dc_text), русский только запасной вариант.
    preview = (dc_text or tg_text or "фото").splitlines()[0][:60] if (dc_text or tg_text) else "фото"
    report  = Report(preview, _src_tag(src))
    bundle  = new_bundle(preview)   # связка ID всех отправок — для /posts

    # Отсчёт — с момента прихода поста. Каждое направление — своя очередь (см. _chain).
    # Фото: сначала проверка картинки (скачивание + OCR, несколько секунд) — дальше уходит
    # только то, что прошло; selfbot-каналы и Rebel Angels — через свою задержку от прихода.
    arrived = time.time()
    bot = context.bot
    is_photo = bool(file_id)
    if not is_photo and not (tg_text or dc_text):
        log("⏭ Skip: пустой пост")
        await send_report(bot, report)
        return
    img_task = asyncio.create_task(_photo_bytes(bot, file_id, bundle)) if is_photo else None
    verdict_task = (asyncio.create_task(_photo_verdict(img_task))
                    if is_photo and not force else None)
    gate = lambda make: (lambda: _if_pass(verdict_task, make))
    ping = _ping_decide(dc_text, src)

    tasks = []
    if is_photo:
        tasks.append(_chain("tg1", gate(lambda: send_tg_photo_main(context, file_id, tg_text, report, bundle))))
    elif tg_text:
        tasks.append(_chain("tg1", lambda: send_tg_text(context, tg_text, "", report, bundle)))
    launchers = []
    if is_photo or dc_text:
        tasks.append(_chain("heaven", gate(lambda: _send_heaven(context, is_photo, dc_text, img_task, report, bundle))))
        tasks.append(_chain("tg3", gate(lambda: _send_tg3(context, is_photo, file_id, dc_text, report, bundle))))
        tasks.append(_chain(src["bee_chain"], gate(lambda: _send_webhook_bee(dc_text, img_task, report, bundle, src))))
        launchers.append(_chain("users", gate(lambda: _launch_users(dc_text, is_photo, img_task, report, bundle))))
        launchers.append(_chain("delayed", gate(lambda: delayed_send(dc_text, img_task, report, bot, file_id,
                                                                     bundle, arrived, ping))))
    post = {"tg_text": tg_text, "dc_text": dc_text, "file_id": file_id, "src": src["id"]}
    asyncio.create_task(_report_when_done(bot, report, bundle, tasks, launchers, verdict_task, post))


def _chat_role(chat_id: int) -> str:
    if chat_id in SOURCES:
        return "это канал-источник ✅"
    names = {TARGET_CHAT_ID: "TG #1", TARGET_CHAT_ID_2: "TG #2", TARGET_CHAT_ID_3: TARGET_LABEL_3}
    if chat_id and chat_id in names:
        return f"это таргет {names[chat_id]} ✅"
    return "в переменных бота этого чата пока нет"


async def on_my_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Бота добавили в канал/группу (или убрали) — админам название и ID: ID не нужно искать вручную,
    а если таргет или источник убрал бота — это сразу видно."""
    cm = update.my_chat_member
    if not cm or cm.chat.type not in ("channel", "group", "supergroup"):
        return
    chat, new, old = cm.chat, cm.new_chat_member, cm.old_chat_member
    title = html.escape(chat.title or "?")
    who = html.escape(cm.from_user.full_name if cm.from_user else "?")
    if new.status == "administrator" and old.status != "administrator":
        can = chat.type != "channel" or getattr(new, "can_post_messages", False)
        text = (f"➕ <b>Меня сделали админом</b>: {title}\nID: <code>{chat.id}</code>\n"
                f"Кто: {who} · {_chat_role(chat.id)}"
                + ("" if can else "\n⚠️ Права «Публикация сообщений» нет — постить туда я не смогу."))
    elif new.status == "member" and old.status in ("left", "kicked"):
        text = (f"➕ <b>Меня добавили</b>: {title}\nID: <code>{chat.id}</code>\n"
                f"Кто: {who} · {_chat_role(chat.id)}")
    elif new.status in ("left", "kicked") and old.status not in ("left", "kicked"):
        text = (f"➖ <b>Меня убрали</b>: {title}\nID: <code>{chat.id}</code>\n"
                f"Кто: {who} · {_chat_role(chat.id)}")
    else:
        return
    log(f"👥 {text}")
    await _deliver_report(ctx.bot, text)


# ── Запуск ────────────────────────────────────────────────────────────────────

def main():
    validate_env()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(user_init)
        .post_shutdown(user_shutdown)
        .concurrent_updates(True)      # команды не ждут, пока пересылается пост
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
    app.add_handler(CallbackQueryHandler(cb_held_force,    pattern=r"^hforce:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_held_drop,     pattern=r"^hdrop:\d+$"))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER))

    print("🚀 Signal filter bot started", flush=True)
    for cid, src in SOURCES.items():
        print(f"📥 Источник{_src_tag(src) or ''}: {cid} · webhook {src['bee_label']}"
              f"{'' if src['bee'] else ' — не задан'}", flush=True)
    if TARGET_CHAT_ID_3:
        print(f"📤 {TARGET_LABEL_3}: {TARGET_CHAT_ID_3} (EN, основной бот)", flush=True)
    # явно: my_chat_member («меня добавили в канал») Telegram по умолчанию может не присылать
    app.run_polling(drop_pending_updates=True,
                    allowed_updates=["message", "channel_post", "callback_query", "my_chat_member"])


if __name__ == "__main__":
    main()
