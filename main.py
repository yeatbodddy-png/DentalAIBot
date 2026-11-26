import time
import asyncio
from datetime import datetime, timedelta
from config import bot, ADMIN_IDS, CLINIC_CITY, CLINIC_ADDRESS
from ai_service import MODELS, initialize_ai_models, warmup_ai_services
from utils import now_almaty_naive
from crm_service import close_crm_session
import handlers  # noqa: F401
from telebot.types import BotCommand
from database import (
    init_db,
    is_encryption_key_configured,
    get_pending_24h_bookings,
    mark_24h_reminded,
    mark_24h_missed,
)

_last_alert_ts: dict[str, float] = {}


async def send_ops_alert(key: str, text: str, cooldown_sec: int = 900):
    """Отправляет администраторам алерт о сбое с защитой от спама раз в cooldown_sec."""
    now_ts = time.monotonic()
    if now_ts - _last_alert_ts.get(key, 0.0) < cooldown_sec:
        return
    _last_alert_ts[key] = now_ts

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            print(f"⚠️ Не удалось отправить алерт администратору {admin_id}: {e}")


async def reminder_24h_worker():
    """
    Фоновая задача: раз в минуту проверяет записи по времени Алматы (GMT+5).
    При простое бота помечает просроченные визиты как пропущенные, чтобы не слать старые напоминания.
    """
    while True:
        try:
            now = now_almaty_naive()
            pending = await get_pending_24h_bookings()

            for booking_id, user_id, doctor, time_slot, appointment_dt, _phone in pending:
                if not appointment_dt:
                    continue
                try:
                    visit_dt = datetime.strptime(appointment_dt, "%Y-%m-%d %H:%M")
                except ValueError:
                    continue

                time_left = visit_dt - now

                # Если визит уже прошёл или просрочен более чем на 2 часа после простоя бота — не шлём задним числом
                if time_left < timedelta(hours=-2):
                    await mark_24h_missed(booking_id)
                    print(f"⏭️ Запись #{booking_id} просрочена при простое бота — помечена как пропущенная.")
                    continue

                # Напоминаем, когда до приёма осталось 24 часа или меньше, но время ещё не прошло
                if timedelta(seconds=0) < time_left <= timedelta(hours=24):
                    text = (
                        "⏰ <b>Напоминание о записи в клинику!</b>\n\n"
                        "Напоминаем, что у вас запланирован приём в течение ближайших 24 часов:\n"
                        f"👨‍⚕️ Врач: <b>{doctor}</b>\n"
                        f"📅 Дата и время (GMT+5): <b>{appointment_dt}</b> (слот {time_slot})\n"
                        f"📍 Адрес: {CLINIC_CITY}, {CLINIC_ADDRESS}\n\n"
                        "Если планы изменились, вы можете перенести или отменить визит в разделе «📋 Мои записи»."
                    )
                    try:
                        await bot.send_message(user_id, text, parse_mode="HTML")
                        await mark_24h_reminded(booking_id)
                        print(f"⏰ Отправлено напоминание за 24ч по записи #{booking_id} (chat_id={user_id})")
                    except Exception as e:
                        print(f"⚠️ Не удалось отправить напоминание по записи #{booking_id} (chat_id={user_id}): {e}")
        except Exception as e:
            print(f"❌ Ошибка в фоновом планировщике напоминаний: {e}")
            await send_ops_alert(
                "reminder_worker_error",
                f"🚨 <b>Сбой воркера напоминаний 24ч:</b>\n<code>{e}</code>"
            )

        await asyncio.sleep(60)


async def main():
    await initialize_ai_models()
    await init_db()

    if not is_encryption_key_configured():
        print("⚠️ ВНИМАНИЕ: DB_ENCRYPTION_KEY не зафиксирован в .env — при перезапуске сменится ключ шифрования БД!")

    asyncio.create_task(warmup_ai_services())
    asyncio.create_task(reminder_24h_worker())

    print(f"📦 Модуль хендлеров загружен: {handlers.__name__}")
    await bot.remove_webhook()

    await bot.set_my_commands([
        BotCommand("start", "Главное меню и выбор режима"),
        BotCommand("reset", "Очистить память диалога"),
        BotCommand("admin", "Панель администратора")
    ])

    print(f"🤖 Найдены актуальные модели: {MODELS}")
    print("✅ Асинхронный DentalBot успешно запущен и ждёт сообщений!")

    try:
        await bot.infinity_polling(timeout=20)
    except Exception as e:
        print(f"❌ Критическая ошибка поллинга: {e}")
        await send_ops_alert(
            "polling_crash",
            f"🚨 <b>Бот упал в цикле опроса Telegram:</b>\n<code>{e}</code>"
        )
        raise
    finally:
        await close_crm_session()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("⚠️ Бот остановлен.")