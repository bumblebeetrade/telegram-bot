import re
import requests
from io import BytesIO
from PIL import Image, ImageFilter
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# === НАСТРОЙКИ ===
BOT_TOKEN = "ТВОЙ_TELEGRAM_BOT_TOKEN"
DISCORD_WEBHOOK_URL = "ТВОЙ_DISCORD_WEBHOOK_URL"
DISCORD_BG_PATH = "discord_bg.png"

# === ФИЛЬТР ТЕКСТА ===
def clean_text(text: str) -> str:
    if not text:
        return ""

    lines = text.split("\n")
    cleaned = []

    for line in lines:
        lower = line.lower()

        # ❌ Удаляем упоминания
        if "@" in line:
            continue

        # ❌ Удаляем ссылки
        if "http" in lower or "t.me" in lower:
            continue

        # ❌ Удаляем рекламные строки
        banned_phrases = [
            "join", "telegram", "channel", "group",
            "dm", "bio", "free", "paid",
            "twitter", "x.com"
        ]

        if any(word in lower for word in banned_phrases):
            continue

        # ❌ Удаляем челлендж (но НЕ completed)
        if "trading challenge" in lower and "completed" not in lower:
            continue

        cleaned.append(line)

    return "\n".join(cleaned).strip()


# === СТИЛИЗАЦИЯ КАРТИНКИ (УЛУЧШЕННАЯ) ===
def stylize_discord_image(image_bytes: bytes) -> bytes:
    original = Image.open(BytesIO(image_bytes)).convert("RGBA")
    background = Image.open(DISCORD_BG_PATH).convert("RGBA").resize(original.size)

    rgb = original.convert("RGB")
    width, height = rgb.size

    mask = Image.new("L", (width, height))
    src = rgb.load()
    dst = mask.load()

    for y in range(height):
        for x in range(width):
            r, g, b = src[x, y]

            brightness = (r + g + b) / 3
            spread = max(r, g, b) - min(r, g, b)

            # 🎯 ЧЁТКО отделяем фон
            if brightness > 210 and spread < 25:
                dst[x, y] = 255
            else:
                dst[x, y] = 0

    # лёгкое сглаживание
    mask = mask.filter(ImageFilter.GaussianBlur(radius=0.8))

    styled = Image.composite(background, original, mask)

    output = BytesIO()
    styled.save(output, format="PNG")
    output.seek(0)
    return output.getvalue()


# === ОТПРАВКА В DISCORD ===
def send_to_discord(text=None, image_bytes=None):
    data = {}
    files = {}

    if text:
        data["content"] = text

    if image_bytes:
        files["file"] = ("image.png", image_bytes, "image/png")

    requests.post(DISCORD_WEBHOOK_URL, data=data, files=files)


# === ОБРАБОТКА СООБЩЕНИЙ ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    text = message.text or message.caption or ""
    cleaned = clean_text(text)

    image_bytes = None

    if message.photo:
        photo = message.photo[-1]
        file = await photo.get_file()
        raw_bytes = await file.download_as_bytearray()

        # 🎨 применяем стиль
        image_bytes = stylize_discord_image(raw_bytes)

    send_to_discord(cleaned, image_bytes)


# === ЗАПУСК БОТА ===
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.ALL, handle_message))

app.run_polling()
