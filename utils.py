import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from config import bot

ALMATY_TZ = ZoneInfo("Asia/Almaty")


def now_almaty() -> datetime:
    """Возвращает текущее время строго по часовому поясу Алматы (GMT+5 / Asia/Almaty)."""
    return datetime.now(ALMATY_TZ)


def now_almaty_naive() -> datetime:
    """Возвращает текущее время по Алматы (GMT+5) без tzinfo для сравнения с '%Y-%m-%d %H:%M'."""
    return now_almaty().replace(tzinfo=None)


def make_py_file(code_text: str, filename: str = "script.py"):
    """Конвертирует строку с кодом в объект для отправки через telebot"""
    file_bytes = code_text.encode("utf-8")
    return (filename, file_bytes)


def get_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("👩‍⚕️ Администратор"),
        KeyboardButton("🦷 Врач-консультант")
    )
    return markup


def _ensure_complete_ending(text: str) -> str:
    """Страхует от обрыва фразы на полуслове."""
    cleaned = text.rstrip()
    if not cleaned:
        return cleaned
    if cleaned[-1].isalnum() and not cleaned.endswith(("₸",)):
        last_line = cleaned.splitlines()[-1]
        if len(last_line.split()) >= 2:
            cleaned = f"{cleaned}…"
    return cleaned


async def send_long_message(message, text, parse_mode="HTML"):
    """Разбивает длинный текст на части по 4000 символов и отправляет с поддержкой HTML (асинхронно)"""
    if not text:
        return
    max_length = 4000
    for i in range(0, len(text), max_length):
        chunk = text[i:i + max_length]
        if i == 0:
            await asyncio.wait_for(bot.reply_to(message, chunk, parse_mode=parse_mode), timeout=12.0)
        else:
            await asyncio.wait_for(bot.send_message(message.chat.id, chunk, parse_mode=parse_mode), timeout=12.0)


async def send_smart_reply(message, text):
    """Отправляет ответ с разметкой; если Telegram ругается на символы — шлёт обычным текстом (асинхронно)."""
    safe_text = _ensure_complete_ending((text or "").strip())
    if not safe_text:
        safe_text = "Извините, не удалось сформировать ответ. Попробуйте, пожалуйста, повторить вопрос."

    for i in range(0, len(safe_text), 4000):
        chunk = safe_text[i:i + 4000]
        try:
            await asyncio.wait_for(
                bot.reply_to(message, chunk, parse_mode="Markdown"),
                timeout=12.0
            )
        except Exception as e:
            print(f"⚠️ Ошибка Markdown рендеринга или сети, шлем чистым текстом: {e}")
            try:
                await asyncio.wait_for(
                    bot.reply_to(message, chunk),
                    timeout=12.0
                )
            except Exception as inner_e:
                print(f"❌ Критическая ошибка отправки сообщения: {inner_e}")


async def notify_all_admins(text, parse_mode="HTML"):
    """
    Надежная асинхронная рассылка уведомлений всем администраторам из ADMIN_IDS.
    """
    from config import ADMIN_IDS  # Импортируем актуальный список ID

    if not ADMIN_IDS:
        print("⚠️ Предупреждение: список ADMIN_IDS пуст, некому отправлять уведомления!")
        return

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=parse_mode)
        except Exception as e:
            print(f"⚠️ Не удалось отправить уведомление администратору {admin_id}: {e}")