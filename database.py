import asyncio
import aiosqlite

DB_NAME = "clinic.db"
_db_lock = asyncio.Lock()


async def init_db():
    """Асинхронная инициализация базы данных и создание таблиц, если их нет"""
    async with _db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute("PRAGMA busy_timeout = 15000;")
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS appointments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    doctor TEXT NOT NULL,
                    time_slot TEXT NOT NULL,
                    appointment_dt TEXT,
                    patient_info TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    reminded_24h INTEGER NOT NULL DEFAULT 0
                )
            """)

            # Безопасная миграция, если appointments уже существовала без новых колонок
            async with db.execute("PRAGMA table_info(appointments)") as cursor:
                columns = {row[1] for row in await cursor.fetchall()}

            if "appointment_dt" not in columns:
                await db.execute("ALTER TABLE appointments ADD COLUMN appointment_dt TEXT")
            if "reminded_24h" not in columns:
                await db.execute("ALTER TABLE appointments ADD COLUMN reminded_24h INTEGER NOT NULL DEFAULT 0")

            await db.execute("""
                CREATE TABLE IF NOT EXISTS booked_slots (
                    doctor TEXT NOT NULL,
                    time_slot TEXT NOT NULL,
                    PRIMARY KEY (doctor, time_slot)
                )
            """)

            await db.commit()


async def add_booking(user_id, doctor, time_slot, patient_info, phone, appointment_dt=None):
    """
    Атомарно бронирует слот и создаёт запись.
    Возвращает True, если слот был свободен и успешно забронирован, иначе False.
    """
    async with _db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute("PRAGMA busy_timeout = 15000;")
            try:
                await db.execute("BEGIN IMMEDIATE;")
                async with db.execute(
                    "SELECT 1 FROM booked_slots WHERE doctor = ? AND time_slot = ?",
                    (doctor, time_slot)
                ) as cursor:
                    already_taken = await cursor.fetchone() is not None

                if already_taken:
                    await db.rollback()
                    return False

                await db.execute(
                    "INSERT INTO booked_slots (doctor, time_slot) VALUES (?, ?)",
                    (doctor, time_slot)
                )
                await db.execute(
                    """
                    INSERT INTO appointments (user_id, doctor, time_slot, appointment_dt, patient_info, phone, reminded_24h)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (user_id, doctor, time_slot, appointment_dt, patient_info, phone)
                )
                await db.commit()
                return True
            except Exception as e:
                await db.rollback()
                print(f"Ошибка при асинхронном добавлении в БД: {e}")
                return False


async def remove_booking_by_id(booking_id):
    """Асинхронное удаление конкретной записи по её уникальному ID"""
    async with _db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute("PRAGMA busy_timeout = 15000;")
            await db.execute("BEGIN IMMEDIATE;")
            async with db.execute("SELECT doctor, time_slot FROM appointments WHERE id = ?", (booking_id,)) as cursor:
                row = await cursor.fetchone()

            booking_data = None
            if row:
                doctor, time_slot = row
                booking_data = {"doctor": doctor, "time": time_slot}
                await db.execute("DELETE FROM booked_slots WHERE doctor = ? AND time_slot = ?", (doctor, time_slot))
                await db.execute("DELETE FROM appointments WHERE id = ?", (booking_id,))
            await db.commit()
            return booking_data


async def is_slot_booked(doctor, time_slot):
    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute(
            "SELECT 1 FROM booked_slots WHERE doctor = ? AND time_slot = ?",
            (doctor, time_slot)
        ) as cursor:
            exists = await cursor.fetchone() is not None
        return exists


async def get_all_bookings():
    """Возвращает все записи в виде словаря {booking_id: {...}}"""
    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute(
            "SELECT id, user_id, doctor, time_slot, appointment_dt, patient_info, phone, reminded_24h FROM appointments"
        ) as cursor:
            rows = await cursor.fetchall()

    bookings = {}
    for row in rows:
        booking_id, user_id, doctor, time_slot, appointment_dt, patient_info, phone, reminded_24h = row
        bookings[booking_id] = {
            "user_id": user_id,
            "doctor": doctor,
            "time": time_slot,
            "appointment_dt": appointment_dt,
            "patient": patient_info,
            "phone": phone,
            "reminded_24h": reminded_24h,
        }
    return bookings


async def get_user_bookings(user_id):
    """Возвращает список всех записей конкретного пользователя"""
    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute(
            "SELECT id, doctor, time_slot, appointment_dt, phone FROM appointments WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return rows


async def get_pending_24h_bookings():
    """Возвращает записи с указанной датой/временем, по которым ещё не отправлено напоминание за 24 часа"""
    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute(
            """
            SELECT id, user_id, doctor, time_slot, appointment_dt, phone
            FROM appointments
            WHERE reminded_24h = 0 AND appointment_dt IS NOT NULL
            """
        ) as cursor:
            return await cursor.fetchall()


async def mark_24h_reminded(booking_id):
    """Помечает запись как напомненную за 24 часа"""
    async with _db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute("PRAGMA busy_timeout = 15000;")
            await db.execute(
                "UPDATE appointments SET reminded_24h = 1 WHERE id = ?",
                (booking_id,)
            )
            await db.commit()