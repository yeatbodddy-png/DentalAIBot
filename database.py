import sqlite3

DB_NAME = "clinic.db"


def init_db():
    """Инициализация базы данных и создание таблиц, если их нет"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Таблица для активных записей пациентов (id теперь PRIMARY KEY AUTOINCREMENT)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            doctor TEXT NOT NULL,
            time_slot TEXT NOT NULL,
            patient_info TEXT NOT NULL
        )
    """)

    # Таблица для занятых слотов (чтобы проверять двойное бронирование)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS booked_slots (
            doctor TEXT NOT NULL,
            time_slot TEXT NOT NULL,
            PRIMARY KEY (doctor, time_slot)
        )
    """)

    conn.commit()
    conn.close()


# Функции для работы с записями
def add_booking(user_id, doctor, time_slot, patient_info):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        # Добавляем в список занятых слотов
        cursor.execute("INSERT OR IGNORE INTO booked_slots (doctor, time_slot) VALUES (?, ?)", (doctor, time_slot))
        # Добавляем новую запись (без перезаписи старых благодаря AUTOINCREMENT)
        cursor.execute(
            "INSERT INTO appointments (user_id, doctor, time_slot, patient_info) VALUES (?, ?, ?, ?)",
            (user_id, doctor, time_slot, patient_info)
        )
        conn.commit()
    except Exception as e:
        print(f"Ошибка при добавлении в БД: {e}")
    finally:
        conn.close()


def remove_booking_by_id(booking_id):
    """Удаление конкретной записи по её уникальному ID"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Находим, какой слот занимала эта конкретная бронь, чтобы освободить его
    cursor.execute("SELECT doctor, time_slot FROM appointments WHERE id = ?", (booking_id,))
    row = cursor.fetchone()

    booking_data = None
    if row:
        doctor, time_slot = row
        booking_data = {"doctor": doctor, "time": time_slot}
        # Удаляем из занятых слотов
        cursor.execute("DELETE FROM booked_slots WHERE doctor = ? AND time_slot = ?", (doctor, time_slot))
        # Удаляем конкретную запись по её id
        cursor.execute("DELETE FROM appointments WHERE id = ?", (booking_id,))
        conn.commit()

    conn.close()
    return booking_data


def is_slot_booked(doctor, time_slot):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM booked_slots WHERE doctor = ? AND time_slot = ?", (doctor, time_slot))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def get_all_bookings():
    """Возвращает все записи в виде словаря {booking_id: {...}}"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, doctor, time_slot, patient_info FROM appointments")
    rows = cursor.fetchall()
    conn.close()

    bookings = {}
    for row in rows:
        booking_id, user_id, doctor, time_slot, patient_info = row
        bookings[booking_id] = {
            "user_id": user_id,
            "doctor": doctor,
            "time": time_slot,
            "patient": patient_info
        }
    return bookings


def get_user_bookings(user_id):
    """Возвращает список всех записей конкретного пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, doctor, time_slot FROM appointments WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows