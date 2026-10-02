"""
Проверка картинок для моста Arki — та же, что у бота 5k-50k (arki-5k-forwarder/filters.py,
раздел «Картинки»; перенесена без изменений). Правила подобраны и проверены на 935 картинках
каналов Arki: карточки позиций, ордера, балансы и графики TradingView проходят; профили и
статистика копитрейдинга, баннеры и бренды бирж, скрины Telegram-каналов, X/Twitter и
Discord, промокоды, почта, расшифрованный QR, ссылки и призывы — блок.

Текст поста по-прежнему фильтрует block_reason в main.py — здесь только картинки.
"""

import re


# ── Картинки: классификация по тексту OCR ────────────────────────────────────

# Промо-признаки на картинке. Голого «copy» здесь нет: на скрине баланса
# биржи есть вкладка «Copy Trading» — это интерфейс, а не реклама.
IMAGE_AD_MARKERS = [
    ("follower", "профиль копитрейдинга (followers)"),
    ("aum", "профиль копитрейдинга (AUM)"),
    ("lead trader", "профиль копитрейдинга"),
    ("copiers", "профиль копитрейдинга"),
    ("my trades", "профиль копитрейдинга"),
    ("trading days", "профиль копитрейдинга"),
    ("profit sharing", "профиль копитрейдинга (profit sharing)"),
    ("profit shared", "профиль копитрейдинга (profit shared)"),
    ("win rate", "статистика копитрейдинга (win rate)"),
    ("win ratio", "статистика копитрейдинга (win ratio)"),
    ("winning positions", "статистика копитрейдинга"),
    ("winning trades", "статистика копитрейдинга"),
    ("losing positions", "статистика копитрейдинга"),
    ("maximum drawdown", "статистика копитрейдинга (drawdown)"),
    ("max drawdown", "статистика копитрейдинга (drawdown)"),
    ("copy immediately", "настройки копитрейдинга"),
    ("start to copy", "настройки копитрейдинга"),
    ("copy ratio", "настройки копитрейдинга"),
    ("crypto arki", "подпись Crypto Arki"),
    ("arkiitrades", "подпись ArkiiTrades"),
    ("arkii trades", "подпись ArkiiTrades"),
    ("better liquidity", "рекламный баннер биржи"),
    ("sign up", "призыв зарегистрироваться"),
    ("signup", "призыв зарегистрироваться"),
    ("register", "призыв зарегистрироваться"),
    ("invite", "инвайт / рефералка"),
    ("referral", "рефералка"),
    ("promo", "промокод"),
    ("bonus", "бонус"),
    ("join", "призыв вступить"),
    ("scan", "QR / призыв"),
    ("http", "ссылка"),
    ("www.", "ссылка"),
    ("t.me", "ссылка t.me"),
]
IMAGE_BRANDS = [
    "bitunix", "binance", "bybit", "blofin", "okx", "bitget", "kucoin", "mexc",
    "bingx", "phemex", "weex", "toobit", "lbank", "htx", "gate.io",
]
CARD_KEYWORDS = [
    "entry", "margin", "liq", "markprice", "mark price", "pnl", "roi",
    "qty", "size", "amount", "orderprice", "order price", "filled",
    "totalvalue", "total value", "balance", "equity",
]


# Скрин чужого Telegram-канала/чата. OCR у нас англо-китайский: русский текст он читает
# латиницей-двойником — «Закреплённое» → «3aKpenneHHoe», «подписчиков» → «noAncKoB»,
# «Промо-код» → «MpoMo-koA». Поэтому ищем и так, и так.
TG_UI_RE = [
    (r"pinned\s*message|3akpen|закреп", "скрин Telegram-канала (закреплённое сообщение)"),
    (r"subscribers|(?<![a-z])members(?![a-z])|подписчик|\dno[aд]n\w{0,3}c\w{0,3}ko[bв]",
     "скрин Telegram-канала (подписчики)"),
    (r"forwarded\s*from|переслано\s*из", "пересланное из другого канала"),
    (r"leave\s*a\s*comment|комментари", "скрин Telegram-канала (комментарии)"),
]
PROMO_RE = [
    (r"(?<![a-z])(?:[nm]pomo|промо)", "промокод"),
    (r"invite\s*code|referral\s*code|ref\s*code", "реферальный код"),
    (r"(?:gmail|yahoo|outlook|hotmail|icloud|proton|yandex)\.(?:com|ru|me)|mail\.ru",
     "почта на картинке (реферальная карточка)"),
]
# Скрины других соцсетей — каждый признак проверен на 922 обычных картинках истории: 0 срабатываний
SOCIAL_RE = [
    (r"(?<![a-z])(?:reposts?|retweets?|bookmarks?|likes)(?![a-z])|translate\s?post|show\s?(?:more|replies)"
     r"|(?<![a-z])(?:x|twitter)\.com", "скрин X/Twitter"),
    (r"today\s?at\s?\d{1,2}:\d{2}", "скрин Discord"),
]
# Лента Telegram: под каждым постом «просмотры время» («382 2:55 PM»), между днями — «September 13».
# Одна такая подпись бывает и у карточки с подписью трейдера — поэтому нужно ≥2 подписи
# или подпись + дата-разделитель (одиночную карточку не режем).
TG_FOOTER_RE = r"\d{1,6}\s?\d{1,2}:\d{2}\s?(?:am|pm)(?![a-z])"
TG_DATE_RE = (r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
              r"\s?\d{1,2}(?!\d)")
# бренды, которые ищем и «почти точно» — OCR теряет буквы («Bitunx» вместо «Bitunix»)
FUZZY_BRANDS = [b for b in IMAGE_BRANDS if len(b) >= 6 and "." not in b]


def _fuzzy_brand(low: str):
    from difflib import SequenceMatcher
    for tok in set(re.findall(r"[a-z0-9]{5,}", low)):
        for brand in FUZZY_BRANDS:
            # 0.9: «bitunx»→bitunix проходит, «finance»→binance (0.86) — нет
            if tok != brand and SequenceMatcher(None, tok, brand).ratio() >= 0.9:
                return brand
    return None


def qr_text(data: bytes) -> str:
    """Текст QR-кода на картинке ('' — нет). Засчитываем только РАСШИФРОВАННЫЙ QR:
    просто «похоже на QR» срабатывает на квадратиках интерфейса бирж (18 карточек из 935),
    а расшифровка — ни на одной обычной карточке из истории канала."""
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return ""
        txt, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        return txt or ""
    except Exception:
        return ""


def image_verdict(ocr_lines: list[str], qr: str = "") -> tuple[bool, str]:
    """(пропустить?, пояснение). Причины собираются все сразу — чтобы было видно,
    что картинка не просто «с логотипом биржи», а, например, чужой канал с промокодом."""
    joined = " ".join(ocr_lines)
    low = joined.lower()
    compact = low.replace(" ", "")
    # причины группируем: «профиль копитрейдинга (followers, AUM, profit sharing)», без повторов
    reasons: dict = {}

    def add(why):
        base, _, detail = why.partition(" (")
        details = reasons.setdefault(base, [])
        detail = detail.rstrip(")")
        if detail and detail not in details:
            details.append(detail)

    for marker, why in IMAGE_AD_MARKERS:
        if " " in marker:
            # фразы: OCR часто склеивает слова — ищем и со склейкой
            found = marker in low or marker.replace(" ", "") in compact
        elif marker[0].isalpha():
            # одно слово — только как отдельное слово: «pairs cancel» не должно давать «scan»,
            # «FuturesBonus» в интерфейсе биржи — не «bonus»
            found = re.search(rf"(?<![a-z]){re.escape(marker)}", low) is not None
        else:
            found = marker in low
        if found:
            add(why)
    # график TradingView («Bitcoin/U.S.Dollar · 4h · Binance») — это анализ, а не реклама биржи
    chart = re.search(r"/\s*(?:u\.?s\.?\s*dollar|tetherus)", low) is not None
    if not chart:
        brand = next((b for b in IMAGE_BRANDS if b in low), None) or _fuzzy_brand(low)
        if brand:
            add(f"бренд биржи «{brand}»")
    # @username — но не почта «…@gmail.com» (её ловит отдельное правило ниже)
    if re.search(r"@[a-z_]{4,}(?![a-z_]*\.[a-z]{2,})", low):
        add("упоминание @username")
    # имя трейдера (скрин его канала, профиль) — такие картинки не пересылаем вовсе
    if re.search(r"\barkii?\b", low):
        add("имя трейдера на картинке")
    for pattern, why in TG_UI_RE + PROMO_RE + SOCIAL_RE:
        if re.search(pattern, low) or re.search(pattern, compact):
            add(why)
    footers = max(len(re.findall(TG_FOOTER_RE, low)), len(re.findall(TG_FOOTER_RE, compact)))
    if footers >= 2 or (footers and re.search(TG_DATE_RE, compact)):
        add("скрин Telegram-канала (просмотры и время постов)")
    if qr:
        add("QR-код со ссылкой")
    if reasons:
        return False, " · ".join(f"{b} ({', '.join(d)})" if d else b for b, d in reasons.items())

    hits = sum(1 for k in CARD_KEYWORDS if k.replace(" ", "") in compact)
    if re.search(r"[a-z0-9]{2,12}usdt", compact) and hits >= 2:
        return True, "карточка позиции / ордера"
    if hits >= 1 and re.search(r"usdt|usd", compact):
        return True, "скрин баланса / счёта"
    if not joined.strip():
        return True, "картинка без текста"
    return True, "картинка без промо-признаков"
