"""
Фильтр для каналов Arki. Один и тот же файл в двух ботах: в 5k-50k (arki-5k-forwarder) это
filters.py, в мосте Arki (telegram-bot) — image_filter.py. Правится в обоих сразу.

Текст: полный стоп-лист моста Arki плюс фразы продажи мест, которые нашлись в канале
Crypto_Arki («Slots are open», «Pay only these addresses», «one time fee 49$», адреса
кошельков для оплаты), призывы к подписчикам (лайки, follow, вступить, оплата, личка) и
правила «наперёд» (FUTURE_REGEX: уведомления, ссылки, бонусы и UID биржи, розыгрыши, спонсоры,
пресейлы, казино, донаты). Маскировка не помогает: «t . me», «b1tunix», «jоin» с русской «о»,
«𝐣𝐨𝐢𝐧», невидимые символы — перед проверкой текст приводится к обычному (normalize_text).
Сигнал с рекламным хвостом не теряется: хвост вырезается, сигнал уходит (cut_ad_tail).

Картинки: текст на картинке читает OCR, дальше — классификация:
  карточка позиции / лимитный ордер / скрин баланса  → пропуск
  профиль копитрейдинга, баннер биржи, ссылки, призывы → блок
"""

import html
import re
import unicodedata
from typing import Optional

# ── Текст: стоп-лист моста Arki (без изменений) ──────────────────────────────

ARKI_BLOCKED_PHRASES = [
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
    "bitunix", "binance", "bybit", "okx", "bitget", "kucoin", "mexc",
    "gate.io", "gateio", "htx", "huobi", "bingx", "phemex", "deribit",
    "coinex", "kraken", "coinbase", "bitmex", "bitfinex", "lbank",
    "pionex", "toobit", "weex", "bitrue", "whitebit", "hyperliquid",
    "bitmart", "poloniex", "bitstamp", "upbit", "bithumb",
    "platform", "exchange",
    "referral", "referal", "ref link", "ref code",
    "promo code", "promocode", "bonus code", "use code", "my code",
    "affiliate", "commission", "management fee",
    "profit share", "pnl share", "bonus",
    "vip signal", "vip group", "vip channel", "vip member",
    "premium group", "premium signal", "premium channel",
    "subscription", "subscribe",
    "mentorship", "webinar", "masterclass", "academy", "trading course",
    "lifetime access", "free access", "free signal", "free trial",
    "prop firm", "funded account",
    "dm me", "pm me", "inbox me", "message me", "text me", "contact me",
    "reach out", "link in bio", "check bio", "bio link", "in bio",
    "join now", "join us", "join here", "invite link", "invite you",
    "my channel", "my group", "our channel", "our group",
    "trade with me", "trade with us", "copy me", "follow my trades",
    "sign up", "signup", "register", "registration",
    "create account", "open account", "new account",
    "discord.gg", "whatsapp", "instagram", "youtube", "tiktok",
    "facebook", "snapchat",
    "guaranteed profit", "guaranteed return", "risk free",
    "slots left", "seats left", "spots left",
    "last chance to join", "limited time offer", "hurry up",
    "click here", "swipe up", "tap the link",
    "double your", "10x your",
]

# ── Текст: продажа мест / оплата — найдено в истории Crypto_Arki ─────────────

SALES_PHRASES = [
    "book slot", "book your slot", "book a slot",
    "be fast", "will be added", "everyone will be added",
    "run private", "private group", "private channel",
    "send screenshot", "after payment", "no other way to pay",
    "thanks for the support", "public challenges",
    "pull out", "copy my next",
]
# Просьбы лайков/ретвитов — не реклама, но и не сигнал
NOISE_PHRASES = ["retweet", "max like", "max support", "shared live on twitter",
                 "repost", "share this", "share with your friend", "tag your friend", "tag a friend",
                 "spread the word", "like button", "smash that", "giveaway", "give away"]
# Призывы к подписчикам — «острое», что не должно уйти: лайки и реакции, подписка на X, оплата и
# цены, вступить в группу / VIP, ссылки «ниже» и в закрепе, личка, бесплатная группа, скачать
# приложение биржи. Живая речь трейдера («Are you guys ready?», «How many of you following the
# trades?», «Next trade is coming») — проходит: так и задумано. Проверено прогоном истории канала
# (сентябрь–ноябрь 2026), X и апрельского архива 200-2k — ни один сигнал не задет.
CALL_REGEX = [
    (r"\b\d+\s*likes?\b", "просьба лайков"),
    (r"\blikes?\s+(?:please|pls|plz|for|if|needed|guys)\b", "просьба лайков"),
    (r"\b(?:drop|give|hit|smash|leave|send|need|want)\b(?:\s+\w+){0,2}\s+(?:likes|a\s+like)\b", "просьба лайков"),
    (r"\b(?:drop|leave|give|send)\s+(?:a\s+|some\s+)?(?:reactions?|❤️|♥️|❤|🔥|👍)", "просьба реакций"),
    (r"\breact(?:ions?)?\s+(?:with|please|pls|if|for|below)\b", "просьба реакций"),
    (r"\bcomment\s+(?:below|done|yes|if)\b", "просьба комментариев"),
    (r"\bfollow\s+(?:me|us|my|our|for|on|the\s+page)\b", "призыв подписаться (follow)"),
    (r"\bfollow\b.{0,25}\b(?:twitter|insta\w*|account|page|youtube|tiktok|x\.com)\b", "призыв подписаться (follow)"),
    (r"\bsub(?:s)?\s+to\b", "призыв подписаться"),
    (r"\bjoin\b.{0,30}\b(?:group|channel|community|discord|vip|premium|copy|link|team|squad|server)\b",
     "призыв вступить"),
    (r"\bvip\b", "VIP"),
    (r"\bfree\s+(?:group|channel|signals?|trial|access|vip|entry|telegram|tg)\b", "бесплатная группа / доступ"),
    (r"\bdm\s+(?:me|us|for|to|now)\b|\binbox\b", "личка (dm)"),
    (r"\blink\s+(?:below|in\s+(?:comments?|description|pinned|bio))\b|\bcheck\s+(?:the\s+)?pinned\b|\bpinned\s+post\b",
     "ссылка «ниже» / в закрепе"),
    (r"\b(?:my|our|this)\s+link\b|\buse\s+(?:my|our)\b", "своя ссылка / код"),
    (r"(?:\$\s*\d+|\d+\s*\$)\s*(?:/|per|a)\s*(?:month|week|year|mo|lifetime)\b|\b(?:monthly|weekly|yearly)\s+"
     r"(?:plan|access|membership)\b|\bmembership\b|\bdiscount\b|\b\d+\s*%\s*off\b|\bpurchase\b|\bbuy\s+(?:my|our|the)\s+"
     r"(?:course|plan|access|membership|signals?)\b", "цена / оплата"),
    (r"\bmirror\s+(?:my|our)\b|\bauto[\s-]?trad", "копитрейдинг"),
    (r"\bdownload\b.{0,20}\bapp\b|\bnon[\s-]?kyc\b|\bno[\s-]?kyc\b", "реклама приложения / биржи"),
]
SALES_REGEX = [
    (r"\bfees?\b", "плата (fee)"),
    (r"\bslots?\b", "продажа мест (slot)"),
    # «Patience paying off» — это про сделку, не про оплату
    (r"\bpay(?:s|ment|ments|ed)?\b|\bpaying\b(?!\s+off)", "оплата (pay)"),
    (r"\bcop(?:y|ies|ied|ying|iers?)\b", "копитрейдинг (copy)"),
    (r"\bscams?\b", "про оплату (scam)"),
    (r"\b(?:bep|trc|erc)-?20\b", "сеть для оплаты"),
    (r"\b0x[0-9a-f]{20,}\b", "адрес кошелька"),
    (r"\bt[1-9a-hj-np-z]{33}\b", "адрес кошелька TRON"),
]
# Наперёд (21.11.2026): такого у Arki ещё не было, но бывает у каналов с сигналами. Каждое правило
# прогнано по истории обоих каналов Arki (июль–ноябрь 2026), его X и апрельскому архиву 200-2k —
# ни один сигнал не задет.
FUTURE_REGEX = [
    (r"\b(?:turn|switch|keep|put)\s+(?:on\s+)?(?:the\s+|your\s+|ur\s+|post\s+)*notifications?\b"
     r"|\bnotifications?\s+on\b|\bbell\s+icon\b", "призыв включить уведомления"),
    (r"\b(?:share|sharing|send|check|click|use|via|through|thru|open)\s+(?:the\s+|my\s+|our\s+|this\s+)?link\b"
     r"|\blinks?\s+(?:is\s+)?(?:above|below|here|in\s+(?:the\s+)?(?:comments?|pinned|channel))\b", "ссылка"),
    (r"\b(?:new|welcome)\s+(?:user\s+)?(?:rewards?|bonus|gifts?)\b|\buser\s+rewards?\b|\bclaim\s+(?:your|the|free|now|rewards?)\b"
     r"|\bfree\s+usdt\b|\d[\d,.]*\+?\s*usdt\s+(?:rewards?|bonus|vouchers?|gifts?)\b", "бонусы биржи"),
    (r"\bvouchers?\b|\bcoupons?\b|\bcash\s*back\b|\brebates?\b|\bfee\s+discount\b|\bkyc\b", "бонусы / KYC биржи"),
    (r"\buid\b", "UID (рефералка биржи)"),
    (r"\bcontests?\b|\braffles?\b|\bprize\s*pool\b|\bprizes?\b|\blucky\s+draw\b|\b(?:claim|free)\s+airdrops?\b",
     "розыгрыш / конкурс"),
    (r"\bsponsor(?:ed|ship|s)?\b|\bpartner(?:ed|ship)?\s+(?:with|program)\b|\bcollab(?:oration)?\b|#ad\b"
     r"|\bpaid\s+promo|\badvertis", "реклама / партнёрство"),
    (r"\bpre[\s-]?sale\b|\bwhitelist\b|\bwl\s+spots?\b|\blaunchpad\b|\bstealth\s+launch\b|\bfair\s+launch\b",
     "пресейл / вайтлист"),
    (r"\bcasino\b|\bbetting\b|\brollbit\b|\bbc\.game\b", "казино / ставки"),
    (r"\b(?:my|our|free|vip|private|paid)\s+(?:tg|discord|whatsapp)\b|\btg\s+(?:group|channel|link)\b"
     r"|\b(?:my|our)\s+(?:twitter|x\s+(?:account|handle|page|profile))\b", "своя группа / соцсеть"),
    (r"\binvit(?:e|ation)\s+(?:code|link|friends?)\b", "инвайт"),
    (r"\b(?:drop|comment|send|share|post)\s+(?:your|ur)\s+(?:uid|wallets?|address|id|emails?|numbers?|usernames?|details)\b",
     "просьба данных"),
    (r"\bdonat(?:e|ion|ions)\b|\bbuy\s+me\s+a\s+coffee\b|\btip\s+(?:me|jar)\b", "донаты"),
    (r"\b(?:trading|signal|signals|copy|auto)\s+bots?\b", "бот-копитрейдинг"),
]
# биржи, которых нет в стоп-листе Arki (не тикеры: ASTER, APEX, DRIFT сюда не попадают)
MORE_EXCHANGES = ["zoomex", "margex", "primexbt", "btcc", "coinw", "ourbit", "deepcoin", "bydfi",
                  "fameex", "bitvenus", "ascendex", "probit"]

# Маскировка: «t . me», «bitunix[.]com», «𝐣𝐨𝐢𝐧», «ᴊᴏɪɴ», «jоin» с русской «о», невидимые символы
_ZERO_WIDTH = re.compile(r"[​-‏⁠-⁤﻿­͏]")
_LOOKALIKE = str.maketrans("аеорсухкіјѕԁοαερτνικᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘʀꜱᴛᴜᴠᴡʏᴢ",
                           "aeopcyxkijsdoaeptvikabcdefghijklmnoprstuvwyz")
_FAKE_DOT = re.compile(r"\s*(?:\[\s*\.\s*\]|\(\s*\.\s*\)|\{\s*\.\s*\}|\[\s*dot\s*\]|\(\s*dot\s*\))\s*"
                       r"|\s+dot\s+(?=(?:com|io|net|org|gg|xyz|link|ly|ee)\b)"
                       r"|(?<=[a-z0-9])\s+\.\s*(?=(?:com|io|net|org|gg|xyz|link|ly|ee)\b)"
                       r"|(?<=\bt)\s+\.\s*(?=me\b)")
_LEET = str.maketrans("013457@$", "oieastas")


def normalize_text(raw_text: str) -> str:
    """Текст для проверки (не для отправки): без маскировки, строчными, пробелы схлопнуты."""
    text = unicodedata.normalize("NFKC", html.unescape(raw_text or ""))
    text = _ZERO_WIDTH.sub("", text).lower().translate(_LOOKALIKE)
    text = re.sub(r"\bhxxp", "http", _FAKE_DOT.sub(".", text))
    return re.sub(r"\s+", " ", text).strip()


def _one_typo(a: str, b: str) -> bool:
    """a и b отличаются ровно одной буквой: заменой, лишней или пропущенной."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


REAL_WORDS = {"finance"}           # настоящее слово в одну букву от биржи («binance») — не биржа


def _exchange_in_text(text: str) -> Optional[str]:
    """Биржа, в том числе замаскированная: «b1tun1x», «bl0fin» (цифры вместо букв) и опечатка в
    длинном названии — «bitunlx», «bitunx». Опечатку ищем только в названиях от 7 букв: короткие
    совпадают с обычными словами («probit» — «profit», 14.09 так резался итог сделки)."""
    for name in MORE_EXCHANGES:
        if re.search(rf"\b{re.escape(name)}\b", text):
            return name
    brands = [b for b in IMAGE_BRANDS + MORE_EXCHANGES if len(b) >= 5 and "." not in b]
    for tok in set(re.findall(r"[a-z0-9@$]{5,}", text)):
        if re.search(r"[a-z]", tok) and re.search(r"[0-9@$]", tok):
            tok = tok.translate(_LEET)                 # «b1tun1x» → «bitunix»
        if tok in REAL_WORDS:
            continue
        for brand in brands:
            if tok == brand or (len(brand) >= 7 and _one_typo(tok, brand)):
                return brand
    return None


def text_block_reason(raw_text: str) -> Optional[str]:
    """Причина блокировки поста целиком. None — пост проходит."""
    if not raw_text or len(raw_text.strip()) < 3:
        return None
    text = normalize_text(raw_text)

    for phrase in ARKI_BLOCKED_PHRASES:
        if phrase in text:
            return f"стоп-фраза «{phrase}»"
    for phrase in SALES_PHRASES:
        if phrase in text:
            return f"продажа мест «{phrase}»"
    for phrase in NOISE_PHRASES:
        if phrase in text:
            return f"просьба лайков «{phrase}»"
    for pattern, why in CALL_REGEX:
        m = re.search(pattern, text)
        if m:
            return f"{why} «{m.group(0).strip()}»"
    for pattern, why in SALES_REGEX:
        if re.search(pattern, text):
            return why
    for pattern, why in FUTURE_REGEX:
        m = re.search(pattern, text)
        if m:
            return f"{why} «{m.group(0).strip()}»"
    brand = _exchange_in_text(text)
    if brand:
        return f"биржа «{brand}»"

    if re.search(r"t\.me/\S+", text):
        return "ссылка t.me"
    if re.search(r"@[a-z_]{4,}", text):
        return "упоминание @username"
    if re.search(
        r"\b[a-z0-9][a-z0-9-]*\.(com|net|org|io|me|co|xyz|app|link|gg|top|vip|pro|site|online|club|finance"
        r"|ly|ee|gl|sh|cc|tv|ai|fi|ru|info|biz|page|store|bio|exchange|trade|cash|money|bet|uk|de|eu|asia"
        r"|cloud|tech|network|global|social|chat)\b",
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


# ── Текст: сигнал + рекламный хвост → вырезаем хвост ─────────────────────────
# Типичный пост: «Long $btc / 2nd entry 86.3k / Sl 83.5k / you can copy my trades
# now. Signup & Deposit…». Реклама всегда в конце — режем с первой рекламной
# строки до конца, сигнал сверху оставляем. Если в начале поста реклама или
# остаток не похож на сделку — пост блокируется целиком, как раньше.

AD_TAIL_RE = re.compile(
    r"cop(?:y|ies|ied|ying|iers?)\b|sign\s*-?\s*up|trade\s+with\s+me|i\s+trade\s+on|"
    r"all\s+(?:my\s+)?(?:the\s+)?(?:challenge\s+)?trades\s+(?:are|will)|partner\.|"
    r"blofin|bingx|bitunix|referr?al|deposit|steps?\s*(?:&|and)\s*conditions|"
    r"join\s+(?:here|now|us)|use\s+my|https?://|t\.me/|register|bonus",
    re.IGNORECASE,
)
TRADE_RE = re.compile(
    r"\b(?:long|short|entry|entries|dca|sl|stops?|tp|targets?|limit|closed?|"
    r"balance|profit|gained|loss|wallet)\b|\$[a-z]{2,}",
    re.IGNORECASE,
)


def _is_call(line: str) -> bool:
    """Строка — призыв к подписчикам (лайки, follow, оплата… — CALL_REGEX, NOISE_PHRASES, FUTURE_REGEX)."""
    low = normalize_text(line)
    return (any(p in low for p in NOISE_PHRASES)
            or any(re.search(p, low) for p, _ in CALL_REGEX + FUTURE_REGEX))


def cut_ad_tail(raw_text: str) -> Optional[str]:
    """Сигнал без рекламного хвоста или None, если так спасти пост нельзя."""
    lines = (raw_text or "").splitlines()
    # «Long $btc / Stops 61k / 100 likes please ♥️» — призыв в конце тоже хвост: сигнал уходит
    cut = next((i for i, ln in enumerate(lines) if AD_TAIL_RE.search(ln) or _is_call(ln)), None)
    if not cut:                      # None — хвоста нет; 0 — реклама с первой строки
        return None
    head = "\n".join(lines[:cut]).strip()
    if not head or not TRADE_RE.search(head) or text_block_reason(head):
        return None
    return head


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
# Наперёд (21.11.2026): баннеры бирж и акций, конкурсы, призывы. Не взяты, хоть и похожи на
# рекламу: «deposit» (кнопка на скрине баланса), «link» (график LINK/USDT), «vip», «uid» (бывают
# в интерфейсе счёта). Каждый признак прогнан по картинкам обоих каналов с июля — 0 срабатываний
# на обычных карточках, балансах и графиках.
FUTURE_IMAGE_RE = [
    (r"(?<![a-z])(?:vouchers?|coupons?|cash\s?back|rebates?)(?![a-z])", "бонусы биржи"),
    (r"(?<![a-z])giveaway|prize\s?pool|(?<![a-z])prizes?(?![a-z])|lucky\s?draw|(?<![a-z])raffle", "розыгрыш"),
    (r"new\s?users?(?![a-z])|welcome\s?(?:bonus|gift|reward)|claim\s?(?:now|your|rewards?)|(?<![a-z])airdrop"
     r"|(?<![a-z])rewards?(?![a-z])|earn\s?up\s?to|get\s?up\s?to|up\s?to\s?\d[\d,]*\s?usdt", "акция биржи"),
    (r"app\s?store|google\s?play|download\s?(?:the\s?)?app|get\s?it\s?on", "реклама приложения"),
    (r"trading\s?(?:competition|contest|tournament)|leaderboard|limited\s?time|campaign", "конкурс / акция"),
    (r"(?<![a-z])subscribe|(?<![a-z])follow\s?(?:us|me|for)|retweet|comment\s?below|like\s?(?:and|&)\s?share",
     "призыв подписаться"),
    (r"(?<![a-z])kyc(?![a-z])|telegram", "реклама (KYC / Telegram)"),
]
# Скрины других соцсетей — каждый признак проверен на 922 обычных картинках истории: 0 срабатываний
SOCIAL_RE = [
    (r"(?<![a-z])(?:reposts?|retweets?|bookmarks?|likes)(?![a-z])|translate\s?post|show\s?(?:more|replies)"
     r"|(?<![a-z])(?:x|twitter)\.com", "скрин X/Twitter"),
    (r"today\s?at\s?\d{1,2}:\d{2}", "скрин Discord"),
]
# Лента Telegram: под каждым постом «просмотры время» («382 2:55 PM»), между днями — «September 13».
# 20.11.2026 трейдер выложил скрин своего же поста в канале с карточкой — такие не пересылаем
# вовсе, хватает одной подписи. OCR читает её по-разному: «O1027:03AM» (глаз — как «O»),
# «66 7:05.AM9» (точка вместо пробела).
TG_FOOTER_RE = r"\d{1,6}\s?\d{1,2}[:.]\d{2}[\s.]?(?:am|pm)(?![a-z])"
# время в 12-часовом формате («7:03 AM») — интерфейс Telegram или X; на карточках бирж и
# графиках его нет (проверено на картинках канала и X за сентябрь–ноябрь 2026)
AMPM_RE = r"(?<![\d:])\d{1,2}[:.]\d{2}\s?\.?\s?(?:am|pm)(?![a-z])"
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
    # «Crypto_Arki»: подчёркивание для регулярок — часть слова, «arki» отдельным словом не
    # находилось (20.11 так прошёл скрин его канала) — считаем его пробелом
    low = joined.lower().replace("_", " ")
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
    for pattern, why in TG_UI_RE + PROMO_RE + SOCIAL_RE + FUTURE_IMAGE_RE:
        if re.search(pattern, low) or re.search(pattern, compact):
            add(why)
    if re.search(TG_FOOTER_RE, low) or re.search(TG_FOOTER_RE, compact):
        add("скрин Telegram-канала (просмотры и время поста)")
    elif re.search(AMPM_RE, low) or re.search(AMPM_RE, compact):
        add("скрин ленты соцсети (время поста)")
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
