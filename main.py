import asyncio
from config import bot
from ai_service import MODELS, initialize_ai_models
import handlers  # noqa: F401 (чтобы PyCharm не ругался на неиспользуемый импорт)
from telebot.types import BotCommand
from database import init_db


async def main():
    # 1. Асинхронно собираем актуальные модели Groq при старте
    await initialize_ai_models()

    # 2. Асинхронно инициализируем SQLite базу данных
    await init_db()

    print(f"📦 Модуль хендлеров загружен: {handlers.__name__}")

    await bot.remove_webhook()

    # 3. Установка команд
    await bot.set_my_commands([
        BotCommand("start", "Главное меню и выбор режима"),
        BotCommand("reset", "Очистить память диалога"),
        BotCommand("admin", "Панель администратора")
    ])

    print(f"🤖 Найдены актуальные модели: {MODELS}")
    print("✅ Асинхронный DentalBot успешно запущен в PyCharm и ждёт сообщений!")

    # 4. Чистый запуск без лишнего while True и без skip_pending, уничтожающего входящие тексты
    await bot.infinity_polling(timeout=20,)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("⚠️ Бот остановлен.")