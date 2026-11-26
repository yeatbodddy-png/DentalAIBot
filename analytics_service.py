import csv
import io
from datetime import timedelta
import aiosqlite
from database import DB_NAME, _db_lock, decrypt_sensitive, mask_phone
from utils import now_almaty_naive

_SERVICE_KEYWORDS = {
    "Лечение кариеса / пломба": ("кариес", "пломб", "тіс жегі"),
    "Имплантация": ("имплант",),
    "Брекеты / ортодонтия": ("брекет", "прикус"),
    "Коронки / протезирование": ("коронк", "циркон", "металлокерам", "қаптама"),
    "Виниры": ("винир", "винір"),
    "Удаление зуба / мудрости": ("удален", "мудрост", "жұл", "алу"),
    "Лечение каналов": ("канал", "пульпит", "түбір", "арна"),
    "Чистка / гигиена": ("чистк", "камн", "налет", "тазала"),
}


def detect_service_topic(text: str) -> str | None:
    """Определяет тему стоматологической услуги из текста вопроса пациента."""
    low = (text or "").lower()
    for title, roots in _SERVICE_KEYWORDS.items():
        if any(r in low for r in roots):
            return title
    return None


async def log_analytics_event(user_id: int, event_type: str, meta: str | None = None):
    """Асинхронно записывает событие воронки в SQLite без замедления ответа пользователю."""
    now_str = now_almaty_naive().strftime("%Y-%m-%d %H:%M:%S")
    try:
        async with _db_lock:
            async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
                await db.execute("PRAGMA busy_timeout = 15000;")
                await db.execute(
                    "INSERT INTO analytics_events (user_id, event_type, meta, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, event_type, (meta or "")[:120], now_str),
                )
                await db.commit()
    except Exception as e:
        print(f"⚠️ Ошибка записи аналитики ({event_type}): {e}")


async def get_owner_dashboard(days: int | None = None) -> dict:
    """
    Собирает полную аналитику для владельца клиники за последние `days` дней
    (если days=None — за всё время).
    """
    cutoff_str = None
    if days is not None:
        cutoff_str = (now_almaty_naive() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")

        if cutoff_str:
            async with db.execute(
                "SELECT event_type, user_id, meta FROM analytics_events WHERE created_at >= ?",
                (cutoff_str,),
            ) as cur:
                events = await cur.fetchall()

            async with db.execute(
                """
                SELECT status, doctor, anamnesis, created_at, updated_at
                FROM appointments
                WHERE created_at >= ? OR created_at IS NULL
                """,
                (cutoff_str[:16],),
            ) as cur:
                bookings = await cur.fetchall()
        else:
            async with db.execute("SELECT event_type, user_id, meta FROM analytics_events") as cur:
                events = await cur.fetchall()

            async with db.execute(
                "SELECT status, doctor, anamnesis, created_at, updated_at FROM appointments"
            ) as cur:
                bookings = await cur.fetchall()

    unique_starts = len({uid for et, uid, _ in events if et == "bot_start"})
    price_questions = sum(1 for et, _, _ in events if et == "ai_price_question")
    booking_starts = sum(1 for et, _, _ in events if et == "booking_started")
    slots_selected = sum(1 for et, _, _ in events if et == "slot_selected")
    bookings_completed_ev = sum(1 for et, _, _ in events if et == "booking_completed")

    total_bookings = len(bookings)
    active_bookings = sum(1 for r in bookings if r[0] == "active")
    cancelled_bookings = sum(1 for r in bookings if r[0] == "cancelled")
    rescheduled_bookings = sum(
        1 for r in bookings if r[0] == "active" and r[3] and r[4] and r[3] != r[4]
    )
    acute_pain = sum(1 for r in bookings if r[2] and "острая боль" in r[2].lower())

    completed_count = max(bookings_completed_ev, total_bookings)
    phone_dropoff = max(0, slots_selected - completed_count)
    slot_to_book_conv = round((completed_count / slots_selected) * 100, 1) if slots_selected > 0 else 0.0
    ask_to_book_conv = round((completed_count / price_questions) * 100, 1) if price_questions > 0 else 0.0

    service_counts: dict[str, int] = {}
    for et, _, meta in events:
        if et == "ai_price_question" and meta:
            service_counts[meta] = service_counts.get(meta, 0) + 1
    top_services = sorted(service_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    doctor_counts: dict[str, int] = {}
    for _, doctor, _, _, _ in bookings:
        doctor_counts[doctor] = doctor_counts.get(doctor, 0) + 1
    top_doctors = sorted(doctor_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "days": days,
        "unique_starts": unique_starts,
        "price_questions": price_questions,
        "booking_starts": booking_starts,
        "slots_selected": slots_selected,
        "completed": completed_count,
        "phone_dropoff": phone_dropoff,
        "slot_to_book_conv": slot_to_book_conv,
        "ask_to_book_conv": ask_to_book_conv,
        "active": active_bookings,
        "cancelled": cancelled_bookings,
        "rescheduled": rescheduled_bookings,
        "acute_pain": acute_pain,
        "top_services": top_services,
        "top_doctors": top_doctors,
    }


async def export_owner_csv_report(days: int | None = None) -> bytes:
    """Формирует CSV-файл для Excel со сводкой воронки и реестром записей."""
    stats = await get_owner_dashboard(days=days)
    period_label = f"Последние {days} дн." if days else "За всё время"

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")

    writer.writerow(["ОТЧЁТ ПО РАБОТЕ DENTALBOT", period_label])
    writer.writerow(["Показатель", "Значение"])
    writer.writerow(["Новых запусков (/start)", stats["unique_starts"]])
    writer.writerow(["Вопросов по услугам и ценам", stats["price_questions"]])
    writer.writerow(["Открытий меню записи", stats["booking_starts"]])
    writer.writerow(["Выбрано врач + слот", stats["slots_selected"]])
    writer.writerow(["Завершённых записей", stats["completed"]])
    writer.writerow(["Отвал на этапе телефона", stats["phone_dropoff"]])
    writer.writerow(["Конверсия слот -> запись (%)", stats["slot_to_book_conv"]])
    writer.writerow(["Конверсия вопрос о цене -> запись (%)", stats["ask_to_book_conv"]])
    writer.writerow(["Активных записей сейчас", stats["active"]])
    writer.writerow(["Отменено", stats["cancelled"]])
    writer.writerow(["Перенесено", stats["rescheduled"]])
    writer.writerow(["С острой болью", stats["acute_pain"]])
    writer.writerow([])

    writer.writerow(["ТОП ЗАПРАШИВАЕМЫХ УСЛУГ", "Количество запросов"])
    for srv, cnt in stats["top_services"]:
        writer.writerow([srv, cnt])
    writer.writerow([])

    writer.writerow(["РЕЕСТР ЗАПИСЕЙ"])
    writer.writerow(["ID", "Врач", "Слот", "Дата/время", "Пациент", "Телефон", "Анамнез", "Статус", "Создано"])

    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute(
            """
            SELECT id, doctor, time_slot, appointment_dt, patient_info, phone, anamnesis, status, created_at
            FROM appointments
            ORDER BY id DESC
            """
        ) as cur:
            rows = await cur.fetchall()

    for r in rows:
        b_id, doc, slot, dt, enc_pat, enc_ph, anam, st, created = r
        writer.writerow([
            b_id,
            doc,
            slot,
            dt or "",
            decrypt_sensitive(enc_pat),
            mask_phone(decrypt_sensitive(enc_ph)),
            anam or "",
            st,
            created or "",
        ])

    return buf.getvalue().encode("utf-8-sig")