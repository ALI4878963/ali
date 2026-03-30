[README.md](https://github.com/user-attachments/files/26354213/README.md)
# 🤖 KURUTT AI TRADER — Telegram Bot

## Установка и запуск

### 1. Установите зависимости
```bash
pip install -r requirements.txt
```

### 2. Установите API ключ Anthropic (ОБЯЗАТЕЛЬНО для AI анализа)
```bash
# Linux/Mac:
export ANTHROPIC_API_KEY="ваш_ключ_здесь"

# Windows CMD:
set ANTHROPIC_API_KEY=ваш_ключ_здесь

# Windows PowerShell:
$env:ANTHROPIC_API_KEY="ваш_ключ_здесь"
```

Получить ключ: https://console.anthropic.com/

### 3. Запустите бота
```bash
python bot.py
```

---

## Команды администратора (ID: 6117198446)

| Команда | Описание |
|---------|----------|
| `/grant <user_id>` | Выдать доступ пользователю |
| `/revoke <user_id>` | Забрать доступ |
| `/sendall <текст>` | Рассылка всем пользователям |
| `/stats` | Статистика бота |

---

## Функции бота

### 🤖 AI Сканер
1. Пользователь нажимает "AI Сканер"
2. Выбирает рынок: Бинарные / Форекс / Крипто
3. Выбирает индикаторы (RSI, MACD, BB, EMA, Stoch, Volume)
4. Отправляет скриншот графика
5. Бот анализирует 15–20 секунд и выдаёт сигнал

### 📈 Марафон +5%
- Пользователь вводит баланс
- Бот строит таблицу роста на 30 дней при +5% в день

### 💬 Чат с AI
- Пользователи могут спрашивать про стратегии, паттерны, управление рисками

---

## Структура файлов

```
trading_bot/
├── bot.py          # Основной файл бота
├── requirements.txt
├── users.json      # Автоматически создаётся — список пользователей
└── access.json     # Автоматически создаётся — список с доступом
```

---

## Хостинг (рекомендации)

- **VPS**: DigitalOcean, Hetzner, TimeWeb (~$5/мес)
- **PaaS**: Railway.app (бесплатный tier)
- **Системный сервис**: systemd для автозапуска

### Запуск через screen (Linux):
```bash
screen -S bot
python bot.py
# Ctrl+A, D — отключиться, бот работает в фоне
```
