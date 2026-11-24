import asyncio
import aiohttp
from utils import now_almaty

# Адрес нашего локального симулятора 1С (который крутится на порту 8081)
CRM_BASE_URL = "http://127.0.0.1:8081"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8.0, connect=2.0)
RETRY_STATUSES = {500, 503}
ALMATY_TZ_NAME = "Asia/Almaty"
ALMATY_UTC_OFFSET = "+05:00"

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


def _crm_headers() -> dict[str, str]:
    now = now_almaty()
    return {
        "X-Timezone": ALMATY_TZ_NAME,
        "X-UTC-Offset": ALMATY_UTC_OFFSET,
        "X-Client-Time-Almaty": now.isoformat(),
    }


async def _get_session() -> aiohttp.ClientSession:
    global _session
    async with _session_lock:
        if _session is None or _session.closed:
            _session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
        return _session


async def close_crm_session():
    global _session
    async with _session_lock:
        if _session is not None and not _session.closed:
            await _session.close()
            _session = None


async def fetch_doctors_from_crm(page: int = 1, limit: int = 6):
    """
    Запрашивает из 1С список врачей с пагинацией (по умолчанию по 6 врачей на страницу,
    чтобы не создавать простыню в Telegram).
    При временных ошибках 500/503 делает короткий повтор.
    """
    url = f"{CRM_BASE_URL}/api/doctors"
    params = {"page": page, "limit": limit, "timezone": ALMATY_TZ_NAME}
    max_attempts = 2

    try:
        session = await _get_session()
        for attempt in range(1, max_attempts + 1):
            async with session.get(url, params=params, headers=_crm_headers()) as response:
                if response.status == 200:
                    return await response.json()
                if response.status in RETRY_STATUSES and attempt < max_attempts:
                    print(f"⚠️ 1С вернула {response.status} при получении врачей, повтор через 0.5s...")
                    await asyncio.sleep(0.5)
                    continue
                print(f"⚠️ Ошибка 1С при получении врачей: статус {response.status}")
                return {"doctors": [], "total": 0, "page": 1}
    except Exception as e:
        print(f"❌ Не удалось подключиться к 1С (сервер выключен/задерживает ответ?): {e}")
        return {"doctors": [], "total": 0, "page": 1}


async def fetch_available_slots_from_crm(doctor_name: str):
    """
    Запрашивает из 1С свободные слоты для конкретного врача по времени Алматы (GMT+5).
    При временных ошибках 500/503 делает короткий повтор.
    """
    url = f"{CRM_BASE_URL}/api/slots"
    almaty_date = now_almaty().strftime("%Y-%m-%d")
    params = {
        "doctor_name": doctor_name,
        "date": almaty_date,
        "timezone": ALMATY_TZ_NAME,
    }
    max_attempts = 2

    try:
        session = await _get_session()
        for attempt in range(1, max_attempts + 1):
            async with session.get(url, params=params, headers=_crm_headers()) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("available_slots", [])
                if response.status in RETRY_STATUSES and attempt < max_attempts:
                    print(f"⚠️ 1С вернула {response.status} при запросе слотов для {doctor_name}, повтор через 0.5s...")
                    await asyncio.sleep(0.5)
                    continue
                print(f"⚠️ Ошибка 1С при получении слотов для {doctor_name}: статус {response.status}")
                return []
    except Exception as e:
        print(f"❌ Ошибка соединения с 1С при запросе слотов: {e}")
        return []


async def sync_create_booking_in_crm(booking_data: dict):
    """
    Отправляет созданную запись в 1С с указанием часового пояса Алматы (Asia/Almaty, +05:00).
    """
    url = f"{CRM_BASE_URL}/api/bookings"
    payload = {
        **booking_data,
        "timezone": ALMATY_TZ_NAME,
        "utc_offset": ALMATY_UTC_OFFSET,
        "created_at_almaty": now_almaty().isoformat(),
    }

    try:
        session = await _get_session()
        async with session.post(url, json=payload, headers=_crm_headers()) as response:
            if response.status == 200:
                res_json = await response.json()
                print(f"✅ Успешно синхронизировано с 1С (GMT+5): {res_json}")
                return True
            else:
                err_text = await response.text()
                print(f"⚠️ 1С отклонила запись (статус {response.status}): {err_text}")
                return False
    except Exception as e:
        print(f"❌ Ошибка отправки брони в 1С: {e}")
        return False


async def sync_cancel_booking_in_crm(doctor_name: str, time_slot: str):
    """
    Уведомляет 1С об отмене записи (освобождает слот обратно в 'free').
    """
    url = f"{CRM_BASE_URL}/api/bookings"
    params = {
        "doctor": doctor_name,
        "time": time_slot,
        "timezone": ALMATY_TZ_NAME,
    }

    try:
        session = await _get_session()
        async with session.delete(url, params=params, headers=_crm_headers()) as response:
            if response.status == 200:
                print(f"🗑️ 1С подтвердила отмену записи: врач {doctor_name}, слот {time_slot}")
                return True
            else:
                print(f"⚠️ Ошибка 1С при отмене записи: статус {response.status}")
                return False
    except Exception as e:
        print(f"❌ Ошибка связи с 1С при отмене: {e}")
        return False