import aiosqlite

DB_NAME = "clinic.db"


async def init_db():
    """Асинхронная инициализация базы данных и создание таблиц, если их нет"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица для активных записей пациентов (с колонкой phone)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                doctor TEXT NOT NULL,
                time_slot TEXT NOT NULL,
                patient_info TEXT NOT NULL,
                phone TEXT NOT NULL
            )
        """)

        # Таблица для занятых слотов (для проверки двойного бронирования)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS booked_slots (
                doctor TEXT NOT NULL,
                time_slot TEXT NOT NULL,
                PRIMARY KEY (doctor, time_slot)
            )
        """)

        await db.commit()


# Асинхронные функции для работы с записями
async def add_booking(user_id, doctor, time_slot, patient_info, phone):
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            # Добавляем в список занятых слотов
            await db.execute("INSERT OR IGNORE INTO booked_slots (doctor, time_slot) VALUES (?, ?)", (doctor, time_slot))
            # Добавляем новую запись вместе с номером телефона
            await db.execute(
                "INSERT INTO appointments (user_id, doctor, time_slot, patient_info, phone) VALUES (?, ?, ?, ?, ?)",
                (user_id, doctor, time_slot, patient_info, phone)
            )
            await db.commit()
        except Exception as e:
            print(f"Ошибка при асинхронном добавлении в БД: {e}")


async def remove_booking_by_id(booking_id):
    """Асинхронное удаление конкретной записи по её уникальному ID"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Находим, какой слот занимала эта конкретная бронь, чтобы освободить его
        async with db.execute("SELECT doctor, time_slot FROM appointments WHERE id = ?", (booking_id,)) as cursor:
            row = await cursor.fetchone()

        booking_data = None
        if row:
            doctor, time_slot = row
            booking_data = {"doctor": doctor, "time": time_slot}
            # Удаляем из занятых слотов
            await db.execute("DELETE FROM booked_slots WHERE doctor = ? AND time_slot = ?", (doctor, time_slot))
            # Удаляем конкретную запись по её id
            await db.execute("DELETE FROM appointments WHERE id = ?", (booking_id,))
            await db.commit()

        return booking_data


async def is_slot_booked(doctor, time_slot):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM booked_slots WHERE doctor = ? AND time_slot = ?", (doctor, time_slot)) as cursor:
            exists = await cursor.fetchone() is not None
        return exists


async def get_all_bookings():
    """Возвращает все записи в виде словаря {booking_id: {...}}"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, user_id, doctor, time_slot, patient_info, phone FROM appointments") as cursor:
            rows = await cursor.fetchall()

    bookings = {}
    for row in rows:
        booking_id, user_id, doctor, time_slot, patient_info, phone = row
        bookings[booking_id] = {
            "user_id": user_id,
            "doctor": doctor,
            "time": time_slot,
            "patient": patient_info,
            "phone": phone
        }
    return bookings


async def get_user_bookings(user_id):
    """Возвращает список всех записей конкретного пользователя (включая телефон)"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, doctor, time_slot, phone FROM appointments WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
        return rows