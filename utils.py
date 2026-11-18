from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from config import bot
# utils.py

def make_py_file(code_text: str, filename: str = "script.py"):
  """Конвертирует строку с кодом в объект для отправки через telebot"""
  file_bytes = code_text.encode("utf-8")
  # telebot принимает файлы из памяти в виде кортежа (имя_файла, байты)
  return (filename, file_bytes)

def get_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("👩‍⚕️ Администратор"),
        KeyboardButton("🦷 Врач-консультант")
    )
    return markup

def send_long_message(message, text, parse_mode="HTML"):
    """Разбивает длинный текст на части по 4000 символов и отправляет с поддержкой HTML"""
    max_length = 4000
    for i in range(0, len(text), max_length):
        chunk = text[i:i + max_length]
        if i == 0:
            bot.reply_to(message, chunk, parse_mode=parse_mode)
        else:
            bot.send_message(message.chat.id, chunk, parse_mode=parse_mode)

def send_smart_reply(message, text):
    """Отправляет ответ с разметкой; если Telegram ругается на символы — шлёт обычным текстом."""
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        try:
            bot.reply_to(message, chunk, parse_mode="Markdown")
        except Exception:
            bot.reply_to(message, chunk)


from config import bot


def notify_all_admins(text, parse_mode="HTML"):
    """
    Надежная рассылка уведомлений всем администраторам из ADMIN_IDS.
    Защищена от падения, если один из админов заблокировал бота или удалил чат.
    """
    from config import ADMIN_IDS  # Импортируем актуальный список ID

    if not ADMIN_IDS:
        print("⚠️ Предупреждение: список ADMIN_IDS пуст, некому отправлять уведомления!")
        return

    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, parse_mode=parse_mode)
        except Exception as e:
            # Профессиональный перехват ошибок: логгируем, но не даем циклу упасть
            print(f"⚠️ Не удалось отправить уведомление администратору {admin_id}: {e}")