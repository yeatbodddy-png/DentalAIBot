import os
import csv
import io
import base64
import hashlib
import asyncio
from datetime import datetime
import aiosqlite
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from utils import now_almaty_naive

load_dotenv()

DB_NAME = "clinic.db"
_db_lock = asyncio.Lock()
_fernet_instance: Fernet | None = None
_key_warning_shown = False


def _get_fernet() -> Fernet:
    """
    Возвращает экземпляр Fernet для шифрования/расшифровки ПДн (patient_info, phone).
    Берёт ключ из DB_ENCRYPTION_KEY в .env; если не задан — генерирует на текущую сессию
    и подсказывает, как зафиксировать его в .env.
    """
    global _fernet_instance, _key_warning_shown
    if _fernet_instance is not None:
        return _fernet_instance

    raw_key = (os.getenv("DB_ENCRYPTION_KEY") or "").strip()
    if not raw_key:
        generated_key = Fernet.generate_key().decode("utf-8")
        _fernet_instance = Fernet(generated_key.encode("utf-8"))
        if not _key_warning_shown:
            _key_warning_shown = True
            print(
                "⚠️ DB_ENCRYPTION_KEY не задан в .env! Сгенерирован временный ключ для текущей сессии. "
                "Для постоянной работы в продакшне добавьте в .env:\n"
                f"DB_ENCRYPTION_KEY={generated_key}"
            )
        return _fernet_instance

    try:
        _fernet_instance = Fernet(raw_key.encode("utf-8"))
    except Exception:
        digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
        derived_key = base64.urlsafe_b64encode(digest)
        _fernet_instance = Fernet(derived_key)

    return _fernet_instance


def is_encryption_key_configured() -> bool:
    return bool((os.getenv("DB_ENCRYPTION_KEY") or "").strip())


def encrypt_sensitive(value: str | None) -> str:
    """Шифрует чувствительное поле (ФИО, телефон) перед записью в SQLite."""
    if not value:
        return ""
    f = _get_fernet()
    token = f.encrypt(str(value).encode("utf-8")).decode("utf-8")
    return f"enc::{token}"


def decrypt_sensitive(value: str | None) -> str:
    """
    Расшифровывает чувствительное поле из SQLite.
    Если значение не имеет префикса 'enc::' (например, старая запись до шифрования),
    возвращает его без падения.
    """
    if not value:
        return ""
    text = str(value)
    if not text.startswith("enc::"):
        return text

    token = text[5:]
    try:
        f = _get_fernet()
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        print("⚠️ Не удалось расшифровать запись ПДн: проверьте совпадение DB_ENCRYPTION_KEY в .env.")
        return "[зашифровано]"
    except Exception as e:
        print(f"⚠️ Ошибка при расшифровке поля ПДн: {e}")
        return "[зашифровано]"


def mask_phone(phone: str | None) -> str:
    """Маскирует номер телефона для безопасного вывода в логи: ***1234."""
    if not phone:
        return "не указан"
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


async def init_db():
    """Асинхронная инициализация базы данных и создание таблиц с безопасной миграцией."""
    _get_fernet()
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
                    anamnesis TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    reminded_24h INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)

            async with db.execute("PRAGMA table_info(appointments)") as cursor:
                columns = {row[1] for row in await cursor.fetchall()}

            migrations = {
                "appointment_dt": "ALTER TABLE appointments ADD COLUMN appointment_dt TEXT",
                "anamnesis": "ALTER TABLE appointments ADD COLUMN anamnesis TEXT",
                "status": "ALTER TABLE appointments ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
                "reminded_24h": "ALTER TABLE appointments ADD COLUMN reminded_24h INTEGER NOT NULL DEFAULT 0",
                "created_at": "ALTER TABLE appointments ADD COLUMN created_at TEXT",
                "updated_at": "ALTER TABLE appointments ADD COLUMN updated_at TEXT",
            }
            for col_name, ddl in migrations.items():
                if col_name not in columns:
                    await db.execute(ddl)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS booked_slots (
                    doctor TEXT NOT NULL,
                    time_slot TEXT NOT NULL,
                    PRIMARY KEY (doctor, time_slot)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    meta TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_analytics_events_type_dt ON analytics_events(event_type, created_at)"
            )

            await db.commit()


async def add_booking(
    user_id,
    doctor,
    time_slot,
    patient_info,
    phone,
    appointment_dt=None,
    anamnesis=None,
):
    """
    Атомарно бронирует слот и создаёт запись с зашифрованными patient_info и phone.
    Возвращает ID созданной записи (int) или None, если слот занят/ошибка.
    """
    enc_patient = encrypt_sensitive(patient_info)
    enc_phone = encrypt_sensitive(phone)
    now_str = now_almaty_naive().strftime("%Y-%m-%d %H:%M")

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
                    return None

                await db.execute(
                    "INSERT INTO booked_slots (doctor, time_slot) VALUES (?, ?)",
                    (doctor, time_slot)
                )
                cursor = await db.execute(
                    """
                    INSERT INTO appointments (
                        user_id, doctor, time_slot, appointment_dt,
                        patient_info, phone, anamnesis, status, reminded_24h, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?)
                    """,
                    (
                        user_id,
                        doctor,
                        time_slot,
                        appointment_dt,
                        enc_patient,
                        enc_phone,
                        anamnesis or "Не указан",
                        now_str,
                        now_str,
                    )
                )
                booking_id = cursor.lastrowid
                await db.commit()
                return booking_id
            except Exception as e:
                await db.rollback()
                print(f"Ошибка при асинхронном добавлении в БД: {e}")
                return None


async def release_slot_and_mark_status(booking_id: int, new_status: str = "cancelled"):
    """Переводит запись в указанный статус ('cancelled' и т.д.) и освобождает слот."""
    now_str = now_almaty_naive().strftime("%Y-%m-%d %H:%M")
    async with _db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute("PRAGMA busy_timeout = 15000;")
            await db.execute("BEGIN IMMEDIATE;")
            async with db.execute(
                "SELECT user_id, doctor, time_slot FROM appointments WHERE id = ?",
                (booking_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await db.rollback()
                return None

            user_id, doctor, time_slot = row
            await db.execute(
                "DELETE FROM booked_slots WHERE doctor = ? AND time_slot = ?",
                (doctor, time_slot)
            )
            await db.execute(
                "UPDATE appointments SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now_str, booking_id)
            )
            await db.commit()
            return {"user_id": user_id, "doctor": doctor, "time": time_slot}


async def remove_booking_by_id(booking_id):
    """Удаляет запись администратором и освобождает слот."""
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


async def reschedule_booking(booking_id: int, user_id: int, new_time_slot: str, new_appointment_dt: str | None):
    """
    Атомарно переносит активную запись пользователя на новый слот.
    Возвращает словарь с данными для синхронизации или None, если новый слот занят.
    """
    now_str = now_almaty_naive().strftime("%Y-%m-%d %H:%M")
    async with _db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute("PRAGMA busy_timeout = 15000;")
            try:
                await db.execute("BEGIN IMMEDIATE;")
                async with db.execute(
                    "SELECT doctor, time_slot FROM appointments WHERE id = ? AND user_id = ? AND status = 'active'",
                    (booking_id, user_id)
                ) as cursor:
                    row = await cursor.fetchone()

                if not row:
                    await db.rollback()
                    return None

                doctor, old_time_slot = row

                if new_time_slot != old_time_slot:
                    async with db.execute(
                        "SELECT 1 FROM booked_slots WHERE doctor = ? AND time_slot = ?",
                        (doctor, new_time_slot)
                    ) as cursor:
                        if await cursor.fetchone() is not None:
                            await db.rollback()
                            return None

                    await db.execute(
                        "DELETE FROM booked_slots WHERE doctor = ? AND time_slot = ?",
                        (doctor, old_time_slot)
                    )
                    await db.execute(
                        "INSERT INTO booked_slots (doctor, time_slot) VALUES (?, ?)",
                        (doctor, new_time_slot)
                    )

                await db.execute(
                    """
                    UPDATE appointments
                    SET time_slot = ?, appointment_dt = ?, reminded_24h = 0, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_time_slot, new_appointment_dt, now_str, booking_id)
                )
                await db.commit()
                return {
                    "doctor": doctor,
                    "old_time": old_time_slot,
                    "new_time": new_time_slot,
                    "appointment_dt": new_appointment_dt,
                }
            except Exception as e:
                await db.rollback()
                print(f"⚠️ Ошибка при переносе записи #{booking_id}: {e}")
                return None


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
    """Возвращает активные записи в виде словаря {booking_id: {...}} с расшифрованными ПДн."""
    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute(
            """
            SELECT id, user_id, doctor, time_slot, appointment_dt, patient_info, phone, anamnesis, reminded_24h
            FROM appointments
            WHERE status = 'active'
            ORDER BY id DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()

    bookings = {}
    for row in rows:
        booking_id, user_id, doctor, time_slot, appointment_dt, enc_patient, enc_phone, anamnesis, reminded_24h = row
        bookings[booking_id] = {
            "user_id": user_id,
            "doctor": doctor,
            "time": time_slot,
            "appointment_dt": appointment_dt,
            "patient": decrypt_sensitive(enc_patient),
            "phone": decrypt_sensitive(enc_phone),
            "anamnesis": anamnesis or "Не указан",
            "reminded_24h": reminded_24h,
        }
    return bookings


async def get_user_bookings(user_id):
    """Возвращает активные записи конкретного пользователя с расшифрованным телефоном."""
    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute(
            """
            SELECT id, doctor, time_slot, appointment_dt, phone, anamnesis
            FROM appointments
            WHERE user_id = ? AND status = 'active'
            ORDER BY id ASC
            """,
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()

    result = []
    for booking_id, doctor, time_slot, appointment_dt, enc_phone, anamnesis in rows:
        result.append((
            booking_id,
            doctor,
            time_slot,
            appointment_dt,
            decrypt_sensitive(enc_phone),
            anamnesis or "Не указан",
        ))
    return result


async def get_pending_24h_bookings():
    """Возвращает активные записи с датой/временем, по которым ещё не обработано напоминание за 24 часа."""
    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute(
            """
            SELECT id, user_id, doctor, time_slot, appointment_dt, phone
            FROM appointments
            WHERE status = 'active' AND reminded_24h = 0 AND appointment_dt IS NOT NULL
            """
        ) as cursor:
            rows = await cursor.fetchall()

    result = []
    for booking_id, user_id, doctor, time_slot, appointment_dt, enc_phone in rows:
        result.append((
            booking_id,
            user_id,
            doctor,
            time_slot,
            appointment_dt,
            decrypt_sensitive(enc_phone),
        ))
    return result


async def mark_24h_reminded(booking_id):
    """Помечает запись как напомненную за 24 часа."""
    now_str = now_almaty_naive().strftime("%Y-%m-%d %H:%M")
    async with _db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute("PRAGMA busy_timeout = 15000;")
            await db.execute(
                "UPDATE appointments SET reminded_24h = 1, updated_at = ? WHERE id = ?",
                (now_str, booking_id)
            )
            await db.commit()


async def mark_24h_missed(booking_id):
    """Помечает просроченное во время простоя бота напоминание как пропущенное (reminded_24h = -1)."""
    now_str = now_almaty_naive().strftime("%Y-%m-%d %H:%M")
    async with _db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute("PRAGMA busy_timeout = 15000;")
            await db.execute(
                "UPDATE appointments SET reminded_24h = -1, updated_at = ? WHERE id = ?",
                (now_str, booking_id)
            )
            await db.commit()


async def get_clinic_analytics() -> dict:
    """Собирает ключевые метрики по записям для владельца клиники."""
    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute("SELECT status, doctor, anamnesis, created_at, updated_at FROM appointments") as cursor:
            rows = await cursor.fetchall()

    total = len(rows)
    active = sum(1 for r in rows if r[0] == "active")
    cancelled = sum(1 for r in rows if r[0] == "cancelled")
    rescheduled = sum(1 for r in rows if r[0] == "active" and r[3] and r[4] and r[3] != r[4])
    acute_pain = sum(1 for r in rows if r[2] and ("острая боль" in r[2].lower() or "боль" in r[2].lower()))

    by_doctor: dict[str, int] = {}
    for _, doctor, _, _, _ in rows:
        by_doctor[doctor] = by_doctor.get(doctor, 0) + 1

    top_doctors = sorted(by_doctor.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "total": total,
        "active": active,
        "cancelled": cancelled,
        "rescheduled": rescheduled,
        "acute_pain": acute_pain,
        "top_doctors": top_doctors,
    }


async def build_bookings_csv_bytes() -> bytes:
    """Формирует CSV-выгрузку записей (с маскированными телефонами) для отчёта администратору."""
    async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
        await db.execute("PRAGMA busy_timeout = 15000;")
        async with db.execute(
            """
            SELECT id, doctor, time_slot, appointment_dt, patient_info, phone, anamnesis, status, created_at
            FROM appointments
            ORDER BY id DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["ID", "Врач", "Слот", "Дата/время", "Пациент", "Телефон", "Анамнез", "Статус", "Создано"])
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