from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from config import bot

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

async def send_long_message(message, text, parse_mode="HTML"):
    """Разбивает длинный текст на части по 4000 символов и отправляет с поддержкой HTML (асинхронно)"""
    max_length = 4000
    for i in range(0, len(text), max_length):
        chunk = text[i:i + max_length]
        if i == 0:
            await bot.reply_to(message, chunk, parse_mode=parse_mode)
        else:
            await bot.send_message(message.chat.id, chunk, parse_mode=parse_mode)

async def send_smart_reply(message, text):
    """Отправляет ответ с разметкой; если Telegram ругается на символы — шлёт обычным текстом (асинхронно)."""
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        try:
            # Сначала пробуем отправить как Markdown
            await bot.reply_to(message, chunk, parse_mode="Markdown")
        except Exception as e:
            print(f"⚠️ Ошибка Markdown рендеринга, шлем чистым текстом: {e}")
            try:
                # Если упало — шлем без разметки как обычный текст
                await bot.reply_to(message, chunk)
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