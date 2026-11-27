"""
Тег роли Discord на важные действия трейдера. Один и тот же файл в мосте Arki (telegram-bot)
и в 5k-50k (arki-5k-forwarder) — правится в обоих сразу.

Зачем: в сообществе Minted у подписчиков с ролью @Crypto звенит уведомление, когда трейдер
  • открывает позицию        «Long zest / 2nd dca 0.11 / Stops 0.107», «SHORT:$SKYAI/USDT ENTRY PRICE…»
  • доливает по рынку        «2nd dca here», «Entered 2nd dca at 3.12», «Added 100$ in pippin»
  • ставит / переносит стоп  «Stops changed to 3.41», «Keep Sl above 0.0211», «Tp 3.71»
  • закрывает руками         «Closed», «Closed zest half for -8», «Closing for -15», «Took some profits»
  • отменяет ордер           «Cancel 2nd dca», «Remove the limit order»
  • переворачивается         «Flip short», «Reverse to long», «Re-entry here»
  • в минусе без стопа       «Lost» / «-100», когда у сделки стопа не было (значит, закрыл руками)
Без тега: убыток по стопу («Sl hit», «Lost», когда стоп был) — его и так все видят; итоги
(«Gained 24», «Balance-569»), «Both entries filled», «Holding», разговор.

Переменная DISCORD_PING — в каком канале какую роль тегать, через запятую:
    DISCORD_PING = Minted:123456789012345678
слева — название канала (как в DISCORD_CHANNELS) или id канала Discord, справа — id роли.
Пусто — тегов нет нигде, всё работает как раньше.

Тег — последней строкой сообщения (<@&id роли>). После тега 2 минуты следующие сообщения идут
без тега: трейдер часто пишет одну сделку двумя сообщениями подряд («Icnt long», через минуту
«Stops 0.311») — один тег на одно событие. Проверено прогоном постов обоих каналов Arki и его
X за 05–21.11.2026: ни одно отдельное событие не осталось без тега.
"""

import os
import re
import time

COOLDOWN = 120                 # сек после тега, когда следующие сообщения идут без тега
TRADE_MEMORY = 3 * 86400       # сколько помнить, был ли у сделки стоп
MAX_LEN = 300                  # длиннее — разговор или реклама, а не действие по сделке


def parse_config(raw: str) -> dict:
    """«Minted:1005…, 123456789012345678:1005…» → {"minted": "1005…", "1234…": "1005…"}.
    Роль можно вставить и тегом «<@&1005…>» — берутся только цифры."""
    out = {}
    for part in re.split(r"[,;\n]+", raw or ""):
        key, _, role = part.strip().rpartition(":")
        role = re.sub(r"\D", "", role)
        key = key.strip().lower()
        if key and role:
            out[key] = role
    return out


CONFIG = parse_config(os.getenv("DISCORD_PING", ""))


def role_for(name: str, channel_id) -> str | None:
    """id роли для этого канала (по названию или id канала) или None — тегов там нет."""
    return CONFIG.get((name or "").strip().lower()) or CONFIG.get(str(channel_id or "").strip())


def unmatched(channels) -> list:
    """Ключи DISCORD_PING, которым не нашлось канала: channels — [(название, id), …]."""
    known = {str(x).strip().lower() for pair in channels for x in pair if x}
    return [k for k in CONFIG if k not in known]


def with_tag(text: str, role: str) -> str:
    return f"{text}\n<@&{role}>" if text else f"<@&{role}>"


# ── Что за сообщение ─────────────────────────────────────────────────────────

_HEADER = re.compile(r"^.*\bchallenge\b.*$|^\s*trade\s*-?\s*\d+\s*$|^\s*announcement\b.*$", re.I | re.M)
# стоп сработал — все и так видят
_SL_HIT = re.compile(r"\b(?:sl|stops?|stop[\s-]*loss)\s*(?:got\s+|was\s+|is\s+)?(?:hit|triggered|taken)\b"
                     r"|\bstopped\s*out\b|\bhit\s+(?:the\s+|my\s+)?(?:sl|stop)\b|\bliquidat(?:ed|ion)\b", re.I)
# закрытие руками — подписчикам надо закрыть тоже (и в минус: «Closed for -100»). Слово «закрыть» —
# только про позицию: в первых словах строки («Closed», «BTC long closed», «I m closing EVAA at 2.567»).
# 27.11 «In less than 12 hours this group will be closed» получил тег — так больше нельзя.
_CLOSE_WORD = re.compile(r"^(?:clos(?:e|ed|ing)|exit(?:ed|ing)?|book(?:ed|ing)?)$")
_CLOSE_ANY = re.compile(r"\b(?:took|take|taking|secure[ds]?|securing|book)\s+(?:some\s+|partial\s+|half\s+)?profits?\b"
                        r"|\btp\s*\d?\s*(?:hit|done)\b|\btargets?\s*\d?\s*(?:hit|done|reached)\b"
                        r"|\bcut\s+(?:the\s+|this\s+)?(?:trade|position|loss)|\bpartial(?:ly)?\s+clos", re.I)
# отмена ордера — подписчикам надо отменить тоже («Cancel 2nd dca», «Remove the limit order»)
_CANCEL = re.compile(r"\b(?:cancel(?:led|ed|ing)?|remove[d]?|delete[d]?)\b[^\n]{0,25}\b(?:orders?|limits?|entr(?:y|ies)|dca|tp|sl|stops?)\b",
                     re.I)
# переворот и перезаход: «Flip short», «Reverse to long», «Re-entry here»
_FLIP = re.compile(r"\bflip(?:ped|ping)?\b|\brevers(?:e|ed|ing)\s+(?:to|position|the\s+position)\b|\bre-?entr(?:y|ies|ered)\b",
                   re.I)
# убыток: тег — только если у сделки не было стопа (значит, закрыл руками). Тоже только в начале строки
# («Lost», «Lost pieverse-89», «Loss day today», «83 lost»): «The loss will be covered» — не убыток.
_LOSS_LINE = re.compile(r"^\s*(?:lost|loss)\b|^\s*-?\s*\$?\d[\d.,]*\s*\$?\s+lost\b"
                        r"|^\s*\$?[a-z][a-z0-9]{1,14}\s+lost\b", re.I | re.M)   # «Aster lost -74»
# речь о группе, канале, челлендже, реакциях — не действие по сделке, тега нет никогда
_META = re.compile(r"\b(?:groups?|channels?|community|chats?|servers?|challenges?|reactions?|likes?|join(?:ing)?"
                   r"|pinned|fees?|subscribers?|members?|onboard\w*|goodbye|farewell)\b", re.I)


def _close_like(t: str) -> bool:
    if _CLOSE_ANY.search(t):
        return True
    for line in t.lower().splitlines():
        words = re.findall(r"[a-z0-9$#@.'-]+", line)
        if any(_CLOSE_WORD.match(w) for w in words[:4]):
            return True
    return False
_LOSS_NUM = re.compile(r"^\s*-\s*\d+(?:[.,]\d+)?\s*\$?\s*\S{0,3}\s*$")          # «-100», «-100 😔», «-19$»
# новая позиция («how long», «long term» — не сделка)
_OPEN = re.compile(r"(?<!how )(?<!as )(?<!so )\b(?:re[\s-]?)?(?:long(?:ed|ing)?|short(?:ed|ing)?)\b"
                   r"(?!\s*(?:term|time|run|while|story))|#(?:long|short)\b", re.I)
# сигнал шаблоном без long/short: «EVAA/USDT / 1st ENTRY:3.53 / … / STOP LOSS: 3.25»
_TEMPLATE = re.compile(r"\bentry(?:\s*price)?\s*[:;\-]+\s*\d.*\bstop", re.I | re.S)
# добор по рынку / добавил маржу
_DCA_NOW = re.compile(r"\bdca\s+here\b|^\s*(?:i\s+)?entered\b"
                      r"|\b(?:entered|entering|added|adding)\b.*\b(?:dca|entry|margin|more|\d+\s*\$|\$\s*\d+)"
                      r"|\b(?:dca|entry)\b.*\bhere\b|\badded\s+\d+\s*\$|\bone\s+more\b.*\b(?:entry|margin)\b", re.I)
_STOP_WORD = r"\b(?:sl|stops?|stop[\s-]*loss)\b"
_LEVEL_TAIL = r"[^\n]{0,30}?(?:\d|\.\d|entry|break\s*-?\s*even|\bbe\b|cost)"
# стоп или тейк поставил / перенёс
_LEVEL = re.compile(rf"(?:{_STOP_WORD}|\btp\d?\b|\btake[\s-]*profit\b|\btargets?\b){_LEVEL_TAIL}", re.I)
# у сделки есть стоп (для решения по «Lost»): «Stops 0.107», «Sl : 0.2», «Stops given», «Keep Sl above…»
_HAS_STOP = re.compile(rf"{_STOP_WORD}(?:{_LEVEL_TAIL}|\s*(?:given|set|above|below))", re.I)
_NOT_TRADE = re.compile(r"\bdon'?t\b|\bwould\b|\bshould\b|\bif\s+you\b|\?\s*$", re.I)

# монета: $FHE, MMT/USDT, SKYAIUSDT, слово рядом с действием («Long fhe», «Fhe long», «Closed zest»)
_ANCHORS = {"long", "longed", "longing", "relong", "short", "shorted", "shorting", "reshort", "closed",
            "close", "closing", "lost", "for", "holding", "hold", "in"}
_NOT_COIN = {
    "long", "longed", "longing", "relong", "short", "shorted", "shorting", "reshort", "limit", "closed",
    "close", "closing", "lost", "loss", "sl", "hit", "tp", "stop", "stops", "stopped", "set", "to", "at",
    "for", "in", "of", "on", "the", "a", "an", "and", "or", "dca", "entry", "entries", "entered", "filled",
    "both", "half", "more", "added", "add", "margin", "with", "profit", "gained", "gain", "balance", "total",
    "left", "average", "avg", "usdt", "usd", "usdc", "perp", "trading", "trade", "challenge", "again", "now",
    "position", "spot", "futures", "leverage", "lev", "my", "is", "it", "still", "open", "k", "x", "i", "we",
    "today", "here", "also", "then", "as", "running", "first", "second", "third", "cmp", "price", "market",
    "guys", "next", "this", "that", "from", "re", "one", "fun", "day", "lol", "bad", "good", "nice",
}


def _clean(text: str) -> str:
    return _HEADER.sub(" ", (text or "").replace("’", "'"))


def coins(text: str) -> set:
    t = _clean(text).lower()
    found = {m.group(1) for m in re.finditer(r"\$([a-z][a-z0-9]{1,14})\b", t)}
    found |= {m.group(1) for m in re.finditer(r"\b([a-z0-9]{2,15})\s*/\s*usdt\b", t)}
    found |= {m.group(1) for m in re.finditer(r"\b([a-z0-9]{2,15})usdt\b", t)}
    words = re.findall(r"[a-z][a-z0-9]*", t)
    for i, w in enumerate(words):
        if w not in _ANCHORS:
            continue
        nxt = words[i + 1:i + 3]
        if nxt and nxt[0] not in _NOT_COIN and len(nxt[0]) >= 2:
            found.add(nxt[0] + nxt[1] if len(nxt) > 1 and w in ("long", "short") and nxt[1] == "verse"
                      else nxt[0])                       # «pie verse» → pieverse
        if w in ("long", "short", "longed", "shorted") and i and words[i - 1] not in _NOT_COIN:
            found.add(words[i - 1])                      # «Fhe long», «Icnt short»
    return {c for c in found if c not in _NOT_COIN and not c.isdigit()}


def classify(text: str) -> str | None:
    """Чистое правило без памяти: «вход», «добор», «стоп/тейк», «закрытие», «убыток», «стоп сработал»
    или None — не действие по сделке."""
    t = _clean(text)
    if not t.strip() or t.lstrip().startswith("@"):
        return None                                      # ответ человеку в X
    if not re.search(r"[a-z]", t, re.I):
        return "убыток" if _LOSS_NUM.match(t) else None  # «-100» — убыток; «+52», «322» — итог
    if _SL_HIT.search(t):
        return "стоп сработал"
    if len(t) > MAX_LEN or _META.search(t):
        return None                                      # длинный текст, речь о группе — не действие
    if _close_like(t):
        return "закрытие"
    if _CANCEL.search(t):
        return "отмена ордера"
    if _LOSS_LINE.search(t):
        return "убыток"
    if _NOT_TRADE.search(t):
        return None                                      # вопросы, советы, разговор
    if _OPEN.search(t) or _TEMPLATE.search(t) or _FLIP.search(t):
        return "вход"
    if _DCA_NOW.search(t):
        return "добор"
    if _LEVEL.search(t):
        return "стоп/тейк"
    return None


class Pinger:
    """Решает по каждому разосланному посту, ставить ли тег. Помнит, был ли у сделки стоп, и
    паузу COOLDOWN после тега. Вызывать для КАЖДОГО разосланного текста, по порядку."""

    def __init__(self):
        self.last = 0.0
        self.trades: dict = {}          # монета (или «?») → {"stop": bool, "at": время}
        self.current = None             # сделка, о которой писали последней

    def decide(self, text: str, now: float | None = None) -> str | None:
        """Что тегаем («вход», «закрытие», …) или None — без тега."""
        now = time.time() if now is None else now
        for k in [k for k, v in self.trades.items() if now - v["at"] > TRADE_MEMORY]:
            del self.trades[k]
        kind = classify(text)
        cs = coins(text)
        stop = bool(_HAS_STOP.search(_clean(text)))
        key = None
        if kind == "вход":
            key = sorted(cs)[0] if cs else "?"
            self.trades[key] = {"stop": stop, "at": now}
            self.current = key
        elif kind or stop:
            # сделка, о которой речь: монета из текста, если о ней уже писали, иначе — последняя.
            # Новая монета без входа («Closed pipin» после перезапуска) — своя запись; для «Lost»
            # незнакомое слово монетой не считаем.
            key = next((c for c in sorted(cs) if c in self.trades), None)
            if key is None and cs and kind not in (None, "убыток"):
                key = sorted(cs)[0]
            key = key or self.current
            if key is not None:
                trade = self.trades.setdefault(key, {"stop": False, "at": now})
                trade["stop"] = trade["stop"] or stop
                if kind == "отмена ордера" and re.search(_STOP_WORD, text, re.I):
                    trade["stop"] = False                # «Remove Sl» — дальше сделка без стопа
                trade["at"] = now
                self.current = key
        if kind == "стоп сработал":
            return None
        if kind == "убыток":
            trade = self.trades.get(key) if key else None
            if trade and trade["stop"]:
                return None                              # убыток по стопу — все и так видят
            kind = "закрытие в минус (стопа не было)"
        if kind is None or now - self.last < COOLDOWN:
            return None
        self.last = now
        return kind
