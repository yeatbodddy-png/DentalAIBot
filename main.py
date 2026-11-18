import time
from config import bot
from ai_service import MODELS
import handlers  # Импортируем хендлеры, чтобы телебот подцепил все декораторы
from telebot.types import BotCommand
from database import init_db  # Импортируем функцию инициализации базы данных

if __name__ == "__main__":
    # Инициализируем SQLite базу данных перед запуском бота (создаст таблицы, если их нет)
    init_db()
    # Обращаемся к handlers, чтобы PyCharm не считал импорт неиспользуемым
    print(f"📦 Модуль хендлеров загружен: {handlers.__name__}")

    bot.remove_webhook()

    bot.set_my_commands([
        BotCommand("start", "Главное меню и выбор режима"),
        BotCommand("reset", "Очистить память диалога")
    ])

    print(f"🤖 Найдены актуальные модели: {MODELS}")
    print("✅ Бот успешно запущен в PyCharm, подключен к SQLite и ждёт сообщений!")

    # Бесконечный цикл с автопереподключением при потере связи с Telegram
    while True:
        try:
            bot.infinity_polling(
                skip_pending=True,
                timeout=60,
                long_polling_timeout=60
            )
        except Exception as e:
            print(f"⚠️ Сбой соединения с Telegram: {e}")
            print("🔄 Пробую переподключиться через 5 секунд...")
            time.sleep(5)