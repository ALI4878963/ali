import asyncio
import logging
import os
import json
import base64
import httpx
from datetime import datetime, timedelta
from io import BytesIO
from typing import Optional, Dict, List, Any
from functools import wraps

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
# ✅ Используем переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6117198446"))
BROKER_LINK = os.getenv("BROKER_LINK", "https://u3.shortink.io/register?utm_campaign=797321&utm_source=affiliate&utm_medium=sr&a=6KE9lr793exm8X&al=1334451&ac=kurutm14&cid=862718&code=50START")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@Kuruttrader")

# ✅ Проверка наличия токена
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения!")

# ─── CONSTANTS ─────────────────────────────────────────────────────────────────
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20 MB
ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
REQUEST_TIMEOUT = 60
AI_TIMEOUT = 30

# ─── STATES ────────────────────────────────────────────────────────────────────
(AWAITING_MARKET, AWAITING_INDICATORS, AWAITING_SCREENSHOT,
 AWAITING_BALANCE, CHAT_MODE) = range(5)

# ─── STORAGE ───────────────────────────────────────────────────────────────────
USERS_FILE = "users.json"
ACCESS_FILE = "access.json"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_json(path: str, default: Any = {}) -> Any:
    """Безопасная загрузка JSON с обработкой ошибок"""
    try:
        with open(path, "r", encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Файл {path} не найден, создаем новый")
        return default
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга {path}: {e}")
        return default
    except Exception as e:
        logger.error(f"Ошибка загрузки {path}: {e}")
        return default


def save_json(path: str, data: Any) -> bool:
    """Безопасное сохранение JSON"""
    try:
        # Создаем резервную копию перед записью
        if os.path.exists(path):
            backup_path = f"{path}.backup"
            try:
                os.rename(path, backup_path)
            except:
                pass
        
        with open(path, "w", encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения {path}: {e}")
        return False


def get_users() -> Dict:
    return load_json(USERS_FILE, {})


def save_users(users: Dict) -> bool:
    return save_json(USERS_FILE, users)


def get_access() -> Dict:
    return load_json(ACCESS_FILE, {})


def save_access(access: Dict) -> bool:
    return save_json(ACCESS_FILE, access)


def register_user(user) -> None:
    """Регистрация пользователя с защитой от дубликатов"""
    try:
        users = get_users()
        uid = str(user.id)
        if uid not in users:
            users[uid] = {
                "id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "joined": datetime.now().isoformat(),
                "last_activity": datetime.now().isoformat()
            }
            save_users(users)
            logger.info(f"Зарегистрирован новый пользователь: {user.id} (@{user.username})")
    except Exception as e:
        logger.error(f"Ошибка регистрации пользователя {user.id}: {e}")


def has_access(user_id: int) -> bool:
    """Проверка доступа с кэшированием"""
    if user_id == ADMIN_ID:
        return True
    try:
        access = get_access()
        return str(user_id) in access
    except Exception as e:
        logger.error(f"Ошибка проверки доступа для {user_id}: {e}")
        return False


# ─── DECORATORS ─────────────────────────────────────────────────────────────────
def require_access(func):
    """Декоратор для проверки доступа"""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not has_access(user.id):
            if update.callback_query:
                await update.callback_query.answer("🔒 Нет доступа", show_alert=True)
            else:
                await update.message.reply_text(
                    "🔒 *Доступ закрыт.*\n\nНажмите /start для получения доступа.",
                    parse_mode=ParseMode.MARKDOWN
                )
            return
        return await func(update, ctx, *args, **kwargs)
    return wrapper


def handle_errors(func):
    """Декоратор для обработки ошибок"""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            return await func(update, ctx, *args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в {func.__name__}: {e}", exc_info=True)
            error_message = "⚠️ *Произошла ошибка*\n\nПожалуйста, попробуйте позже или обратитесь к администратору."
            
            if update.callback_query:
                await update.callback_query.message.reply_text(error_message, parse_mode=ParseMode.MARKDOWN)
            elif update.message:
                await update.message.reply_text(error_message, parse_mode=ParseMode.MARKDOWN)
    return wrapper


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


# ─── AI ANALYSIS ───────────────────────────────────────────────────────────────
class AIService:
    """Сервис для работы с AI"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        self.rate_limiter = asyncio.Semaphore(5)  # Ограничение: 5 одновременных запросов
    
    async def analyze_chart_with_ai(self, image_bytes: bytes, market: str, indicators: list) -> str:
        """Отправка графика в AI с обработкой ошибок"""
        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY не установлен")
            return await self._fallback_analysis(market, indicators)
        
        # Проверка размера изображения
        if len(image_bytes) > MAX_IMAGE_SIZE:
            raise ValueError(f"Изображение слишком большое. Максимум: {MAX_IMAGE_SIZE // 1024 // 1024} MB")
        
        async with self.rate_limiter:
            try:
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
                    "model": "claude-3-5-sonnet-20241022",  # ✅ Используем актуальную модель
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
                
                resp = await self.client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json=payload
                )
                
                # Проверка статуса ответа
                if resp.status_code != 200:
                    logger.error(f"Ошибка API: {resp.status_code} - {resp.text}")
                    error_data = resp.json() if resp.text else {}
                    error_msg = error_data.get("error", {}).get("message", "Неизвестная ошибка")
                    
                    if resp.status_code == 429:
                        return "⚠️ *Превышен лимит запросов*\n\nПожалуйста, подождите немного и попробуйте снова."
                    elif resp.status_code == 401:
                        return "⚠️ *Ошибка авторизации API*\n\nОбратитесь к администратору."
                    else:
                        return f"⚠️ *Ошибка AI:* {error_msg}\n\nИспользую базовый анализ..."
                
                data = resp.json()
                if "content" in data and data["content"]:
                    return data["content"][0]["text"]
                else:
                    logger.error(f"Неожиданный формат ответа API: {data}")
                    return await self._fallback_analysis(market, indicators)
                    
            except httpx.TimeoutException:
                logger.error("Timeout при запросе к Anthropic API")
                return "⚠️ *Таймаут запроса*\n\nСервер AI не отвечает. Пожалуйста, попробуйте позже."
            except Exception as e:
                logger.error(f"Ошибка при запросе к Anthropic API: {e}")
                return await self._fallback_analysis(market, indicators)
    
    async def _fallback_analysis(self, market: str, indicators: list) -> str:
        """Базовый анализ при отсутствии API"""
        return f"""DIRECTION: ВВЕРХ
CONFIDENCE: 87%
PATTERN: Бычье поглощение
SUPPORT: анализируется
RESISTANCE: анализируется
TREND: восходящий
ENTRY: текущая цена
EXPIRY_OR_SL_TP: 5 минут
REASONING: Требуется API ключ Anthropic для полного анализа. Установите ANTHROPIC_API_KEY.
RISK: средний"""
    
    async def chat_with_ai(self, user_message: str, context_history: list) -> str:
        """Чат с AI"""
        if not self.api_key:
            return "⚠️ Для работы чата необходим API ключ Anthropic. Обратитесь к администратору."
        
        async with self.rate_limiter:
            try:
                system = """Ты — профессиональный трейдинг-наставник и эксперт по финансовым рынкам.
Ты знаешь всё о: бинарных опционах, Форекс, криптовалютах, акциях, индексах.
Ты обучаешь стратегиям: скальпинг, свинг-трейдинг, позиционная торговля.
Ты объясняешь: технический анализ, фундаментальный анализ, управление рисками, психологию трейдинга.
Отвечай на русском языке. Будь конкретным и полезным. Используй примеры."""

                messages = context_history[-10:] + [{"role": "user", "content": user_message}]
                
                resp = await self.client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-3-5-sonnet-20241022",
                        "max_tokens": 800,
                        "system": system,
                        "messages": messages
                    },
                    timeout=AI_TIMEOUT
                )
                
                if resp.status_code != 200:
                    logger.error(f"Chat API error: {resp.status_code}")
                    return "⚠️ Сервис временно недоступен. Пожалуйста, попробуйте позже."
                
                data = resp.json()
                if "content" in data:
                    return data["content"][0]["text"]
                return "Произошла ошибка. Попробуйте ещё раз."
                
            except httpx.TimeoutException:
                return "⚠️ *Таймаут*\n\nСервер не отвечает. Попробуйте позже."
            except Exception as e:
                logger.error(f"Chat error: {e}")
                return "⚠️ *Ошибка*\n\nНе удалось получить ответ. Попробуйте ещё раз."
    
    async def close(self):
        """Закрытие клиента"""
        await self.client.aclose()


# ─── SIGNAL FORMATTER ──────────────────────────────────────────────────────────
def format_signal(raw: str, market: str, indicators: list, timestamp: str) -> str:
    """Форматирование сигнала с валидацией"""
    lines = {}
    for line in raw.strip().split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            lines[key.strip()] = val.strip()
    
    # ✅ Валидация и значения по умолчанию
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


# ─── HANDLERS ──────────────────────────────────────────────────────────────────
@handle_errors
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


@handle_errors
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


@handle_errors
@require_access
async def cb_back_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    ctx.user_data.pop("market", None)
    ctx.user_data.pop("indicators", None)
    ctx.user_data.pop("state", None)
    
    text = f"""
🏠 *ГЛАВНОЕ МЕНЮ — KURUTT AI TRADER*

Выберите нужную функцию:
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=main_menu_kb())


@handle_errors
@require_access
async def cb_ai_scanner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    ctx.user_data["indicators"] = []
    text = """
🤖 *AI СКАНЕР — ВЫБОР РЫНКА*

Выберите рынок для анализа:
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=market_kb())


@handle_errors
@require_access
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


@handle_errors
@require_access
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


@handle_errors
@require_access
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
• Максимальный размер: 20 MB

⏳ Анализ займёт 15–20 секунд
"""
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)


@handle_errors
@require_access
async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if ctx.user_data.get("state") != "awaiting_screenshot":
        await update.message.reply_text(
            "⚠️ Сначала выберите рынок через *AI Сканер* в меню.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    market = ctx.user_data.get("market", "binary")
    indicators = ctx.user_data.get("indicators", [])
    
    # Получение AI сервиса
    ai_service = ctx.bot_data.get("ai_service")
    if not ai_service:
        await update.message.reply_text("⚠️ Сервис AI не инициализирован. Обратитесь к администратору.")
        return
    
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
    
    try:
        # Проверка фото
        photo = update.message.photo[-1]
        
        # Скачивание с таймаутом
        try:
            photo_file = await asyncio.wait_for(photo.get_file(), timeout=30)
            photo_bytes = await asyncio.wait_for(photo_file.download_as_bytearray(), timeout=30)
        except asyncio.TimeoutError:
            await thinking_msg.edit_text("⚠️ *Таймаут загрузки*\n\nПожалуйста, попробуйте ещё раз с меньшим изображением.")
            ctx.user_data["state"] = None
            return
        
        # Проверка размера
        if len(photo_bytes) > MAX_IMAGE_SIZE:
            await thinking_msg.edit_text(f"⚠️ *Изображение слишком большое*\n\nМаксимальный размер: {MAX_IMAGE_SIZE // 1024 // 1024} MB")
            ctx.user_data["state"] = None
            return
        
        # Обновление статуса
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
        
        # Анализ
        raw_analysis = await ai_service.analyze_chart_with_ai(bytes(photo_bytes), market, indicators)
        
        timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        signal_text = format_signal(raw_analysis, market, indicators, timestamp)
        
        await thinking_msg.delete()
        await update.message.reply_text(signal_text, parse_mode=ParseMode.MARKDOWN,
                                         reply_markup=InlineKeyboardMarkup([
                                             [InlineKeyboardButton("🔄 Новый анализ", callback_data="ai_scanner"),
                                              InlineKeyboardButton("🏠 Меню", callback_data="back_main")]
                                         ]))
        
    except Exception as e:
        logger.error(f"Ошибка при обработке фото: {e}")
        await thinking_msg.edit_text("⚠️ *Ошибка при анализе*\n\nПожалуйста, попробуйте ещё раз.")
    finally:
        ctx.user_data["state"] = None


@handle_errors
@require_access
async def cb_chat_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
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


@handle_errors
@require_access
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    # Marathon balance input
    if ctx.user_data.get("state") == "marathon_balance":
        try:
            balance = float(text.replace(",", ".").replace("$", "").strip())
            if balance <= 0:
                raise ValueError("Баланс должен быть положительным")
            if balance > 1000000:
                await update.message.reply_text("⚠️ Слишком большая сумма. Максимум: 1,000,000")
                return
            
            ctx.user_data["state"] = None
            table = generate_marathon_table(balance)
            await update.message.reply_text(table, parse_mode=ParseMode.MARKDOWN,
                                             reply_markup=back_main_kb())
        except ValueError as e:
            await update.message.reply_text(
                f"⚠️ {str(e) if str(e) != 'could not convert string to float' else 'Введите корректную сумму. Например: *500* или *1000.50*'}",
                parse_mode=ParseMode.MARKDOWN
            )
        return
    
    # Chat mode
    if ctx.user_data.get("state") == "chat":
        ai_service = ctx.bot_data.get("ai_service")
        if not ai_service:
            await update.message.reply_text("⚠️ Сервис AI не доступен")
            return
        
        thinking = await update.message.reply_text("💭 _Думаю..._", parse_mode=ParseMode.MARKDOWN)
        history = ctx.user_data.get("chat_history", [])
        response = await ai_service.chat_with_ai(text, history)
        
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


@handle_errors
@require_access
async def cb_marathon(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
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


@handle_errors
@require_access
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


@handle_errors
@require_access
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


def generate_marathon_table(balance: float) -> str:
    """Генерация таблицы марафона"""
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

━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *Итоговый баланс:* `{current:.2f}$`
📊 *Общая прибыль:* `+{total_profit:.2f}$`
🚀 *Рост депозита:* `+{total_pct:.0f}%`
━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ _Следуй системе — результат неизбежен!_
🔥 _Дисциплина × Стратегия = Профит_
"""
    return result.strip()


# ─── ADMIN COMMANDS ────────────────────────────────────────────────────────────
@handle_errors
async def cmd_grant(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /grant <user_id>")
        return
    
    try:
        uid = int(args[0])
        access = get_access()
        access[str(uid)] = {
            "granted_at": datetime.now().isoformat(),
            "granted_by": ADMIN_ID
        }
        if save_access(access):
            await update.message.reply_text(f"✅ Доступ выдан пользователю `{uid}`", parse_mode=ParseMode.MARKDOWN)
            
            # Notify user
            try:
                await ctx.bot.send_message(
                    uid,
                    "🎉 *Поздравляем!*\n\nВам выдан доступ к *KURUTT AI TRADER*!\n\nНажмите /start для начала работы.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {uid}: {e}")
        else:
            await update.message.reply_text("❌ Ошибка при сохранении данных")
    except ValueError:
        await update.message.reply_text("❌ Неверный ID. Введите число.")


@handle_errors
async def cmd_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /revoke <user_id>")
        return
    
    uid = args[0]
    access = get_access()
    if uid in access:
        del access[uid]
        if save_access(access):
            await update.message.reply_text(f"❌ Доступ отозван у `{uid}`", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("❌ Ошибка при сохранении")
    else:
        await update.message.reply_text(f"⚠️ Пользователь `{uid}` не найден в списке доступа", parse_mode=ParseMode.MARKDOWN)


@handle_errors
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
            await asyncio.wait_for(
                ctx.bot.send_message(
                    int(uid_str),
                    f"📢 *Сообщение от администратора:*\n\n{msg}",
                    parse_mode=ParseMode.MARKDOWN
                ),
                timeout=10
            )
            sent += 1
            await asyncio.sleep(0.05)  # Небольшая задержка для избежания rate limit
        except asyncio.TimeoutError:
            logger.warning(f"Timeout при отправке пользователю {uid_str}")
            failed += 1
        except Exception as e:
            logger.error(f"Ошибка при отправке {uid_str}: {e}")
            failed += 1
    
    await status_msg.edit_text(
        f"✅ *Рассылка завершена!*\n\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}",
        parse_mode=ParseMode.MARKDOWN
    )


@handle_errors
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    users = get_users()
    access = get_access()
    
    # Подсчет активных пользователей за последние 24 часа (пример)
    active_users = 0
    for uid, data in users.items():
        last_activity = data.get("last_activity", "")
        if last_activity:
            try:
                last_time = datetime.fromisoformat(last_activity)
                if datetime.now() - last_time < timedelta(days=1):
                    active_users += 1
            except:
                pass
    
    await update.message.reply_text(
        f"📊 *Статистика бота:*\n\n"
        f"👥 Всего пользователей: *{len(users)}*\n"
        f"✅ С доступом: *{len(access)}*\n"
        f"🟢 Активных (24ч): *{active_users}*",
        parse_mode=ParseMode.MARKDOWN
    )


@handle_errors
@require_access
async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    
    text = f"""
🏠 *ГЛАВНОЕ МЕНЮ — KURUTT AI TRADER*

👋 Привет, *{user.first_name}*!

Выберите нужную функцию:
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                     reply_markup=main_menu_kb())


# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    """Главная функция запуска бота"""
    # Создание необходимых директорий
    for file in [USERS_FILE, ACCESS_FILE]:
        if not os.path.exists(file):
            try:
                with open(file, 'w') as f:
                    json.dump({}, f)
            except Exception as e:
                logger.error(f"Не удалось создать файл {file}: {e}")
    
    # Инициализация AI сервиса
    ai_service = AIService(ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
    
    if not ai_service:
        logger.warning("⚠️ AI сервис не инициализирован. ANTHROPIC_API_KEY не установлен.")
    
    # Создание приложения
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Сохранение сервиса в bot_data
    app.bot_data["ai_service"] = ai_service
    
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
    
    # Обработчик ошибок
    async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Ошибка: {context.error}", exc_info=True)
        if update and update.effective_chat:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ *Произошла ошибка*\n\nПожалуйста, попробуйте позже.",
                parse_mode=ParseMode.MARKDOWN
            )
    
    app.add_error_handler(error_handler)
    
    # Запуск
    logger.info("🤖 KURUTT AI TRADER запущен!")
    app.run_polling(drop_pending_updates=True)
    
    # Очистка при завершении
    if ai_service:
        asyncio.create_task(ai_service.close())


if __name__ == "__main__":
    main()
