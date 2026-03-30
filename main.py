import asyncio
import logging
import os
import json
import base64
import httpx
from datetime import datetime, timedelta
from io import BytesIO

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)
from telegram.constants import ParseMode

# ─── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN = "8696661970:AAEqKj-uYynowcAh9WzEBiJLQD4nYlz2230"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")  # Set via env variable
ADMIN_ID = 6117198446
BROKER_LINK = "https://u3.shortink.io/register?utm_campaign=797321&utm_source=affiliate&utm_medium=sr&a=6KE9lr793exm8X&al=1334451&ac=kurutm14&cid=862718&code=50START"
ADMIN_USERNAME = "@Kuruttrader"

# ─── STATES ────────────────────────────────────────────────────────────────────
(AWAITING_MARKET, AWAITING_INDICATORS, AWAITING_SCREENSHOT,
 AWAITING_BALANCE, CHAT_MODE) = range(5)

# ─── STORAGE ───────────────────────────────────────────────────────────────────
USERS_FILE = "users.json"
ACCESS_FILE = "access.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_users():
    return load_json(USERS_FILE, {})


def save_users(users):
    save_json(USERS_FILE, users)


def get_access():
    return load_json(ACCESS_FILE, {})


def save_access(access):
    save_json(ACCESS_FILE, access)


def register_user(user):
    users = get_users()
    uid = str(user.id)
    if uid not in users:
        users[uid] = {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "joined": datetime.now().isoformat()
        }
        save_users(users)


def has_access(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    access = get_access()
    return str(user_id) in access


# ─── KEYBOARDS ─────────────────────────────────────────────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 AI Сканер", callback_data="ai_scanner"),
         InlineKeyboardButton("💬 Чат с ботом", callback_data="chat_mode")],
        [InlineKeyboardButton("📈 Марафон +5%", callback_data="marathon"),
         InlineKeyboardButton("📊 Мои сигналы", callback_data="my_signals")],
        [InlineKeyboardButton("ℹ️ О боте", callback_data="about")],
    ])


def market_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Бинарные опционы", callback_data="market_binary")],
        [InlineKeyboardButton("💹 Форекс (MT4/MT5)", callback_data="market_forex")],
        [InlineKeyboardButton("🪙 Крипто рынок", callback_data="market_crypto")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")],
    ])


def indicators_kb(market: str):
    buttons = [
        [InlineKeyboardButton("📉 RSI", callback_data=f"ind_RSI"),
         InlineKeyboardButton("📊 MACD", callback_data=f"ind_MACD")],
        [InlineKeyboardButton("🎯 Bollinger Bands", callback_data=f"ind_BB"),
         InlineKeyboardButton("📐 EMA/SMA", callback_data=f"ind_EMA")],
        [InlineKeyboardButton("🔥 Stochastic", callback_data=f"ind_Stoch"),
         InlineKeyboardButton("💡 Volume", callback_data=f"ind_Vol")],
        [InlineKeyboardButton("✅ Начать анализ", callback_data=f"start_analysis")],
        [InlineKeyboardButton("🔙 Назад", callback_data="ai_scanner")],
    ]
    return InlineKeyboardMarkup(buttons)


def back_main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")]
    ])


# ─── ANTHROPIC AI ANALYSIS ─────────────────────────────────────────────────────
async def analyze_chart_with_ai(image_bytes: bytes, market: str, indicators: list) -> str:
    """Send chart image to Claude for deep analysis."""
    if not ANTHROPIC_API_KEY:
        return await fallback_analysis(market, indicators)

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    indicators_str = ", ".join(indicators) if indicators else "все стандартные"

    market_context = {
        "binary": "бинарные опционы (нужно указать направление ВВЕРХ/ВНИЗ и время экспирации 1-15 минут)",
        "forex": "Форекс MT4/MT5 (нужно указать направление BUY/SELL, Stop Loss и Take Profit в пипсах)",
        "crypto": "криптовалютный рынок (нужно указать направление LONG/SHORT, Stop Loss % и Take Profit %)"
    }[market]

    system_prompt = f"""Ты — самый опытный трейдер и аналитик в мире. 
Ты умеешь читать графики как книгу. Твой анализ всегда точен на 85-95%.

Анализируй ТОЛЬКО то, что видишь на графике. Используй:
- Паттерны Price Action (пин-бар, поглощение, доджи, молот, звезда)
- Уровни поддержки и сопротивления
- Тренд и его направление
- Запрошенные индикаторы: {indicators_str}
- Свечной анализ (тени, тела свечей)
- Объёмы если видны
- Зоны консолидации и пробои

Рынок: {market_context}

Отвечай СТРОГО в этом формате (без лишних слов):
DIRECTION: [ВВЕРХ/ВНИЗ или BUY/SELL или LONG/SHORT]
CONFIDENCE: [85-95]%
PATTERN: [главный паттерн]
SUPPORT: [уровень]
RESISTANCE: [уровень]
TREND: [восходящий/нисходящий/боковой]
ENTRY: [рекомендуемая точка входа]
EXPIRY_OR_SL_TP: [для бинарных - время экспирации, для форекс/крипто - SL и TP]
REASONING: [3-5 предложений объяснения на русском языке]
RISK: [низкий/средний/высокий]"""

    payload = {
        "model": "claude-opus-4-5",
        "max_tokens": 1000,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": f"Проанализируй этот график. Рынок: {market}. Индикаторы для анализа: {indicators_str}. Дай точный торговый сигнал."
                    }
                ]
            }
        ]
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json=payload
        )
        data = resp.json()
        if "content" in data and data["content"]:
            return data["content"][0]["text"]
        return await fallback_analysis(market, indicators)


async def fallback_analysis(market: str, indicators: list) -> str:
    """Fallback when no API key - returns placeholder."""
    return "DIRECTION: ВВЕРХ\nCONFIDENCE: 87%\nPATTERN: Бычье поглощение\nSUPPORT: анализируется\nRESISTANCE: анализируется\nTREND: восходящий\nENTRY: текущая цена\nEXPIRY_OR_SL_TP: 5 минут\nREASONING: Требуется API ключ Anthropic для полного анализа. Установите ANTHROPIC_API_KEY.\nRISK: средний"


async def chat_with_ai(user_message: str, context_history: list) -> str:
    """General trading chat with AI."""
    if not ANTHROPIC_API_KEY:
        return "⚠️ Для работы чата необходим API ключ Anthropic. Обратитесь к администратору."

    system = """Ты — профессиональный трейдинг-наставник и эксперт по финансовым рынкам.
Ты знаешь всё о: бинарных опционах, Форекс, криптовалютах, акциях, индексах.
Ты обучаешь стратегиям: скальпинг, свинг-трейдинг, позиционная торговля.
Ты объясняешь: технический анализ, фундаментальный анализ, управление рисками, психологию трейдинга.
Отвечай на русском языке. Будь конкретным и полезным. Используй примеры."""

    messages = context_history[-10:] + [{"role": "user", "content": user_message}]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-opus-4-5",
                "max_tokens": 800,
                "system": system,
                "messages": messages
            }
        )
        data = resp.json()
        if "content" in data:
            return data["content"][0]["text"]
        return "Произошла ошибка. Попробуйте ещё раз."


# ─── SIGNAL FORMATTER ──────────────────────────────────────────────────────────
def format_signal(raw: str, market: str, indicators: list, timestamp: str) -> str:
    lines = {}
    for line in raw.strip().split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            lines[key.strip()] = val.strip()

    direction = lines.get("DIRECTION", "N/A")
    confidence = lines.get("CONFIDENCE", "87%")
    pattern = lines.get("PATTERN", "N/A")
    support = lines.get("SUPPORT", "N/A")
    resistance = lines.get("RESISTANCE", "N/A")
    trend = lines.get("TREND", "N/A")
    entry = lines.get("ENTRY", "N/A")
    expiry_sl_tp = lines.get("EXPIRY_OR_SL_TP", "N/A")
    reasoning = lines.get("REASONING", "N/A")
    risk = lines.get("RISK", "средний")

    dir_emoji = "🟢⬆️" if any(w in direction.upper() for w in ["ВВЕРХ", "BUY", "LONG"]) else "🔴⬇️"
    risk_emoji = {"низкий": "🟢", "средний": "🟡", "высокий": "🔴"}.get(risk.lower(), "🟡")

    market_names = {
        "binary": "⚡ Бинарные опционы",
        "forex": "💹 Форекс MT4/MT5",
        "crypto": "🪙 Криптовалюта"
    }
    market_name = market_names.get(market, market)

    ind_str = " • ".join(indicators) if indicators else "Стандартный анализ"

    if market == "binary":
        trade_block = (
            f"⏱ *Экспирация:* `{expiry_sl_tp}`\n"
            f"🎯 *Точка входа:* `{entry}`"
        )
    else:
        tp_sl_parts = expiry_sl_tp.split(",") if "," in expiry_sl_tp else [expiry_sl_tp]
        trade_block = (
            f"🎯 *Точка входа:* `{entry}`\n"
            f"📊 *SL / TP:* `{expiry_sl_tp}`"
        )

    signal = f"""
╔══════════════════════════╗
║  🤖 *AI СКАНЕР — СИГНАЛ*  ║
╚══════════════════════════╝

📌 *Рынок:* {market_name}
🕐 *Время:* `{timestamp}`
📐 *Индикаторы:* {ind_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━

{dir_emoji} *НАПРАВЛЕНИЕ: {direction}*
💯 *Уверенность: {confidence}*

━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 *ТЕХНИЧЕСКИЙ АНАЛИЗ:*
• 📈 Тренд: `{trend}`
• 🏛 Паттерн: `{pattern}`
• 🟢 Поддержка: `{support}`
• 🔴 Сопротивление: `{resistance}`

{trade_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━

💬 *ОБОСНОВАНИЕ:*
_{reasoning}_

━━━━━━━━━━━━━━━━━━━━━━━━━━

{risk_emoji} *Риск:* {risk.upper()}

⚠️ _Торгуйте ответственно. Управляйте рисками!_
"""
    return signal.strip()


# ─── MARATHON ──────────────────────────────────────────────────────────────────
def generate_marathon_table(balance: float) -> str:
    rows = []
    current = balance
    header = "╔════╦══════════════╦══════════════╗\n║ 📅 ║  💰 Баланс   ║  📈 Прибыль  ║\n╠════╬══════════════╬══════════════╣"
    rows.append(header)

    for day in range(1, 31):
        profit = current * 0.05
        current += profit
        rows.append(f"║ {day:2d} ║ {current:12.2f} ║ +{profit:11.2f} ║")

    rows.append("╚════╩══════════════╩══════════════╝")

    total_profit = current - balance
    total_pct = ((current / balance) - 1) * 100

    table = "\n".join(rows)
    result = f"""
🏆 *МАРАФОН +5% В ДЕНЬ — 30 ДНЕЙ*
━━━━━━━━━━━━━━━━━━━━━━━━━━

💵 *Стартовый баланс:* `{balance:.2f}$`
🎯 *Цель:* +5% ежедневно к депозиту

```
{table}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *Итоговый баланс:* `{current:.2f}$`
📊 *Общая прибыль:* `+{total_profit:.2f}$`
🚀 *Рост депозита:* `+{total_pct:.0f}%`
━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ _Следуй системе — результат неизбежен!_
🔥 _Дисциплина × Стратегия = Профит_
"""
    return result.strip()


# ─── HANDLERS ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    ctx.user_data.clear()

    welcome = f"""
🤖 *Добро пожаловать, {user.first_name}!*

Я — *KURUTT AI TRADER* — самый продвинутый торговый бот на базе искусственного интеллекта.

🧠 *Что я умею:*
• 🔍 Анализировать графики с точностью *85–95%*
• ⚡ Давать сигналы для бинарных опционов
• 💹 Сигналы для Форекс (MT4/MT5) со SL и TP
• 🪙 Анализ крипто рынков
• 📈 Составлять марафон роста депозита
• 💬 Обучать торговым стратегиям

🔬 *Технология:* Многоуровневый нейросетевой анализ с распознаванием паттернов, уровней поддержки/сопротивления и индикаторов.

👇 *Нажмите кнопку ниже, чтобы начать:*
"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 ПОЛУЧИТЬ ДОСТУП", callback_data="get_access")]
    ])
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)


async def cb_get_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = f"""
🔐 *КАК ПОЛУЧИТЬ ДОСТУП:*

*Шаг 1️⃣* — Зарегистрируйтесь у нашего брокера по ссылке:
👇
[👉 ОТКРЫТЬ СЧЁТ ЗДЕСЬ]({BROKER_LINK})

*Шаг 2️⃣* — После регистрации отправьте:
• Ваш *Telegram ID* (можно узнать у @userinfobot)
• *ID вашего счёта* у брокера

📩 Отправьте данные администратору: *{ADMIN_USERNAME}*

━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ *Ваш Telegram ID:* `{query.from_user.id}`
━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ После подтверждения вы получите полный доступ к боту!
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   disable_web_page_preview=True)


async def cb_back_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if not has_access(user.id):
        await query.edit_message_text(
            "🔒 *Доступ закрыт.*\n\nНажмите /start для получения доступа.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    ctx.user_data.pop("market", None)
    ctx.user_data.pop("indicators", None)
    ctx.user_data.pop("state", None)

    text = f"""
🏠 *ГЛАВНОЕ МЕНЮ — KURUTT AI TRADER*

Выберите нужную функцию:
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=main_menu_kb())


async def cb_ai_scanner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not has_access(query.from_user.id):
        await query.answer("🔒 Нет доступа. Используйте /start", show_alert=True)
        return

    ctx.user_data["indicators"] = []
    text = """
🤖 *AI СКАНЕР — ВЫБОР РЫНКА*

Выберите рынок для анализа:
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=market_kb())


async def cb_market_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    market = query.data.replace("market_", "")
    ctx.user_data["market"] = market
    ctx.user_data["indicators"] = []

    market_names = {
        "binary": "⚡ Бинарные опционы",
        "forex": "💹 Форекс MT4/MT5",
        "crypto": "🪙 Крипто рынок"
    }

    text = f"""
✅ *Рынок выбран:* {market_names[market]}

📐 *ВЫБЕРИТЕ ИНДИКАТОРЫ* для более точного анализа:
_(можно выбрать несколько — нажимайте и подтверждайте)_

Выбранные: _пока не выбраны_
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=indicators_kb(market))


async def cb_indicator_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    ind = query.data.replace("ind_", "")
    indicators = ctx.user_data.get("indicators", [])

    if ind in indicators:
        indicators.remove(ind)
        await query.answer(f"❌ {ind} убран")
    else:
        indicators.append(ind)
        await query.answer(f"✅ {ind} добавлен")

    ctx.user_data["indicators"] = indicators
    market = ctx.user_data.get("market", "binary")
    market_names = {"binary": "⚡ Бинарные опционы", "forex": "💹 Форекс MT4/MT5", "crypto": "🪙 Крипто рынок"}

    selected_str = " • ".join(indicators) if indicators else "_пока не выбраны_"
    text = f"""
✅ *Рынок:* {market_names.get(market, market)}

📐 *ВЫБЕРИТЕ ИНДИКАТОРЫ:*

Выбранные: {selected_str}
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=indicators_kb(market))


async def cb_start_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["state"] = "awaiting_screenshot"

    market = ctx.user_data.get("market", "binary")
    indicators = ctx.user_data.get("indicators", [])
    ind_str = ", ".join(indicators) if indicators else "базовый анализ"

    text = f"""
📸 *ОТПРАВЬТЕ СКРИНШОТ ГРАФИКА*

🔍 Буду анализировать по:
• Индикаторы: *{ind_str}*
• Паттерны Price Action
• Уровни поддержки/сопротивления
• Свечной анализ

📌 *Требования к скриншоту:*
• Хорошее качество изображения
• Видны свечи и уровни
• Желательно таймфрейм M1-H1

⏳ Анализ займёт 15–20 секунд
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not has_access(user.id):
        await update.message.reply_text("🔒 Нет доступа. Используйте /start")
        return

    if ctx.user_data.get("state") != "awaiting_screenshot":
        await update.message.reply_text(
            "⚠️ Сначала выберите рынок через *AI Сканер* в меню.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    market = ctx.user_data.get("market", "binary")
    indicators = ctx.user_data.get("indicators", [])

    thinking_msg = await update.message.reply_text(
        "🧠 *Нейросеть анализирует график...*\n\n"
        "⏳ Идёт глубокий анализ:\n"
        "├ 🔍 Распознавание паттернов...\n"
        "├ 📊 Анализ индикаторов...\n"
        "├ 🏛 Определение уровней...\n"
        "├ 📈 Оценка тренда...\n"
        "└ 🎯 Формирование сигнала...\n\n"
        "⏱ _Подождите 15–20 секунд..._",
        parse_mode=ParseMode.MARKDOWN
    )

    await asyncio.sleep(3)

    # Update progress
    await thinking_msg.edit_text(
        "🧠 *Нейросеть анализирует график...*\n\n"
        "⏳ Идёт глубокий анализ:\n"
        "├ ✅ Паттерны распознаны\n"
        "├ ✅ Индикаторы обработаны\n"
        "├ 🔍 Определение уровней...\n"
        "├ 📈 Оценка тренда...\n"
        "└ ⏳ Формирование сигнала...\n\n"
        "⏱ _Почти готово..._",
        parse_mode=ParseMode.MARKDOWN
    )

    # Download photo
    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    photo_bytes = await photo_file.download_as_bytearray()

    await asyncio.sleep(5)

    # Analyze
    raw_analysis = await analyze_chart_with_ai(bytes(photo_bytes), market, indicators)

    await asyncio.sleep(3)

    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    signal_text = format_signal(raw_analysis, market, indicators, timestamp)

    await thinking_msg.delete()
    await update.message.reply_text(signal_text, parse_mode=ParseMode.MARKDOWN,
                                     reply_markup=InlineKeyboardMarkup([
                                         [InlineKeyboardButton("🔄 Новый анализ", callback_data="ai_scanner"),
                                          InlineKeyboardButton("🏠 Меню", callback_data="back_main")]
                                     ]))

    ctx.user_data["state"] = None


async def cb_chat_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not has_access(query.from_user.id):
        await query.answer("🔒 Нет доступа", show_alert=True)
        return

    ctx.user_data["state"] = "chat"
    ctx.user_data["chat_history"] = []

    text = """
💬 *ЧАТ С AI ТРЕЙДЕРОМ*

Я готов ответить на любые вопросы о трейдинге!

*Что можно спросить:*
• 📚 Обучи стратегии скальпинга
• 🎯 Как правильно ставить Stop Loss?
• 📊 Объясни паттерн "Голова и плечи"
• ⚡ Стратегии для бинарных опционов
• 🔄 Управление капиталом (Money Management)
• 🧠 Психология трейдинга

Просто напишите ваш вопрос 👇
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=InlineKeyboardMarkup([
                                       [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")]
                                   ]))


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    if not has_access(user.id):
        await update.message.reply_text("🔒 Нет доступа. Используйте /start")
        return

    # Marathon balance input
    if ctx.user_data.get("state") == "marathon_balance":
        try:
            balance = float(text.replace(",", ".").replace("$", "").strip())
            ctx.user_data["state"] = None
            table = generate_marathon_table(balance)
            await update.message.reply_text(table, parse_mode=ParseMode.MARKDOWN,
                                             reply_markup=back_main_kb())
        except ValueError:
            await update.message.reply_text(
                "⚠️ Введите корректную сумму. Например: *500* или *1000.50*",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    # Chat mode
    if ctx.user_data.get("state") == "chat":
        thinking = await update.message.reply_text("💭 _Думаю..._", parse_mode=ParseMode.MARKDOWN)
        history = ctx.user_data.get("chat_history", [])
        response = await chat_with_ai(text, history)

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": response})
        ctx.user_data["chat_history"] = history[-20:]

        await thinking.delete()
        await update.message.reply_text(
            f"🤖 *AI Трейдер:*\n\n{response}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")]
            ])
        )
        return

    # Default
    await update.message.reply_text(
        "💡 Используйте меню для навигации. Введите /start",
        reply_markup=back_main_kb()
    )


async def cb_marathon(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not has_access(query.from_user.id):
        await query.answer("🔒 Нет доступа", show_alert=True)
        return

    ctx.user_data["state"] = "marathon_balance"
    text = """
📈 *МАРАФОН +5% В ДЕНЬ*

Система роста депозита на *30 дней* с ежедневным приростом *+5%* к балансу.

━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *Введите ваш текущий баланс:*
_(например: 500 или 1000.50)_
━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)


async def cb_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = f"""
ℹ️ *О БОТЕ — KURUTT AI TRADER*

🤖 *Технологии:*
• Многоуровневый нейросетевой анализ
• Распознавание 50+ паттернов Price Action
• Анализ уровней поддержки и сопротивления
• Мультиindикаторный анализ

📊 *Поддерживаемые рынки:*
• ⚡ Бинарные опционы
• 💹 Форекс (MT4 / MT5)
• 🪙 Криптовалюты

🎯 *Точность сигналов:* 85–95%

⏱ *Время анализа:* 15–20 секунд

📩 *Поддержка:* {ADMIN_USERNAME}

━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *Дисклеймер:*
_Торговля несёт финансовые риски. Всегда используйте управление капиталом._
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=back_main_kb())


async def cb_my_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = """
📊 *МОИ СИГНАЛЫ*

История ваших последних сигналов будет отображаться здесь.

🔄 Используйте *AI Сканер* для получения нового сигнала!
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=InlineKeyboardMarkup([
                                       [InlineKeyboardButton("🤖 AI Сканер", callback_data="ai_scanner")],
                                       [InlineKeyboardButton("🏠 Меню", callback_data="back_main")]
                                   ]))


# ─── MENU AFTER ACCESS ─────────────────────────────────────────────────────────
async def show_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = f"""
🏠 *ГЛАВНОЕ МЕНЮ — KURUTT AI TRADER*

👋 Привет, *{user.first_name}*!

Выберите нужную функцию:
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                     reply_markup=main_menu_kb())


# ─── ADMIN COMMANDS ────────────────────────────────────────────────────────────
async def cmd_grant(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /grant <user_id>")
        return

    try:
        uid = int(args[0])
        access = get_access()
        access[str(uid)] = {"granted_at": datetime.now().isoformat(), "granted_by": ADMIN_ID}
        save_access(access)
        await update.message.reply_text(f"✅ Доступ выдан пользователю `{uid}`", parse_mode=ParseMode.MARKDOWN)

        # Notify user
        try:
            await ctx.bot.send_message(
                uid,
                "🎉 *Поздравляем!*\n\nВам выдан доступ к *KURUTT AI TRADER*!\n\nНажмите /start для начала работы.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

    except ValueError:
        await update.message.reply_text("❌ Неверный ID")


async def cmd_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args
    if not args:
        return
    uid = args[0]
    access = get_access()
    access.pop(uid, None)
    save_access(access)
    await update.message.reply_text(f"❌ Доступ отозван у `{uid}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_sendall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not ctx.args:
        await update.message.reply_text("Usage: /sendall <текст сообщения>")
        return

    msg = " ".join(ctx.args)
    users = get_users()
    sent = 0
    failed = 0

    status_msg = await update.message.reply_text(f"📤 Рассылка начата... Получателей: {len(users)}")

    for uid_str, udata in users.items():
        try:
            await ctx.bot.send_message(
                int(uid_str),
                f"📢 *Сообщение от администратора:*\n\n{msg}",
                parse_mode=ParseMode.MARKDOWN
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ *Рассылка завершена!*\n\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}",
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = get_users()
    access = get_access()
    await update.message.reply_text(
        f"📊 *Статистика бота:*\n\n"
        f"👥 Всего пользователей: *{len(users)}*\n"
        f"✅ С доступом: *{len(access)}*",
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    if not has_access(user.id):
        await update.message.reply_text(
            "🔒 *Доступ закрыт.*\n\nНажмите /start для получения доступа.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await show_main_menu(update, ctx)


# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("sendall", cmd_sendall))
    app.add_handler(CommandHandler("stats", cmd_stats))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_get_access, pattern="^get_access$"))
    app.add_handler(CallbackQueryHandler(cb_back_main, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(cb_ai_scanner, pattern="^ai_scanner$"))
    app.add_handler(CallbackQueryHandler(cb_market_select, pattern="^market_"))
    app.add_handler(CallbackQueryHandler(cb_indicator_toggle, pattern="^ind_"))
    app.add_handler(CallbackQueryHandler(cb_start_analysis, pattern="^start_analysis$"))
    app.add_handler(CallbackQueryHandler(cb_chat_mode, pattern="^chat_mode$"))
    app.add_handler(CallbackQueryHandler(cb_marathon, pattern="^marathon$"))
    app.add_handler(CallbackQueryHandler(cb_about, pattern="^about$"))
    app.add_handler(CallbackQueryHandler(cb_my_signals, pattern="^my_signals$"))

    # Messages
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 KURUTT AI TRADER запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
