import asyncio
from datetime import datetime, timedelta
from config import bot
from ai_service import MODELS, initialize_ai_models, warmup_ai_services
import handlers  # noqa: F401
from telebot.types import BotCommand
from database import init_db, get_pending_24h_bookings, mark_24h_reminded


async def reminder_24h_worker():
    """Фоновая задача: раз в минуту проверяет записи и напоминает клиенту за сутки до приёма"""
    while True:
        try:
            now = datetime.now()
            pending = await get_pending_24h_bookings()

            for booking_id, user_id, doctor, time_slot, appointment_dt, phone in pending:
                if not appointment_dt:
                    continue
                try:
                    visit_dt = datetime.strptime(appointment_dt, "%Y-%m-%d %H:%M")
                except ValueError:
                    continue

                time_left = visit_dt - now
                # Напоминаем, когда до приёма осталось 24 часа или меньше, но время ещё не прошло
                if timedelta(seconds=0) < time_left <= timedelta(hours=24):
                    text = (
                        "⏰ <b>Напоминание о записи в клинику!</b>\n\n"
                        "Напоминаем, что у вас запланирован приём в течение ближайших 24 часов:\n"
                        f"👨‍⚕️ Врач: <b>{doctor}</b>\n"
                        f"📅 Дата и время: <b>{appointment_dt}</b> (слот {time_slot})\n"
                        f"📍 Адрес: г. Алматы, ул. Абая 150 (угол ул. Сатпаева)\n\n"
                        "Если планы изменились, пожалуйста, сообщите нам заранее."
                    )
                    try:
                        await bot.send_message(user_id, text, parse_mode="HTML")
                        await mark_24h_reminded(booking_id)
                        print(f"⏰ Отправлено напоминание за 24ч по записи #{booking_id} пользователю {user_id}")
                    except Exception as e:
                        print(f"⚠️ Не удалось отправить напоминание по записи #{booking_id} ({user_id}): {e}")
        except Exception as e:
            print(f"❌ Ошибка в фоновом планировщике напоминаний: {e}")

        await asyncio.sleep(60)


async def main():
    await initialize_ai_models()
    await init_db()
    await warmup_ai_services()

    asyncio.create_task(reminder_24h_worker())

    print(f"📦 Модуль хендлеров загружен: {handlers.__name__}")
    await bot.remove_webhook()

    await bot.set_my_commands([
        BotCommand("start", "Главное меню и выбор режима"),
        BotCommand("reset", "Очистить память диалога"),
        BotCommand("admin", "Панель администратора")
    ])

    print(f"🤖 Найдены актуальные модели: {MODELS}")
    print("✅ Асинхронный DentalBot успешно запущен в PyCharm и ждёт сообщений!")

    await bot.infinity_polling(timeout=20)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("⚠️ Бот остановлен.")