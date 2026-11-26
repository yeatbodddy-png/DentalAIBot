import os
import asyncio
import aiohttp
from dotenv import load_dotenv
from utils import now_almaty

load_dotenv()

CRM_BASE_URL = (os.getenv("CRM_BASE_URL") or "http://127.0.0.1:8081").rstrip("/")
CRM_AUTH_TYPE = (os.getenv("CRM_AUTH_TYPE") or "none").strip().lower()  # none | basic | bearer
CRM_USERNAME = os.getenv("CRM_USERNAME") or ""
CRM_PASSWORD = os.getenv("CRM_PASSWORD") or ""
CRM_API_TOKEN = os.getenv("CRM_API_TOKEN") or ""

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8.0, connect=2.0)
RETRY_STATUSES = {500, 502, 503, 504}
ALMATY_TZ_NAME = "Asia/Almaty"
ALMATY_UTC_OFFSET = "+05:00"

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


def _crm_headers() -> dict[str, str]:
    now = now_almaty()
    headers = {
        "X-Timezone": ALMATY_TZ_NAME,
        "X-UTC-Offset": ALMATY_UTC_OFFSET,
        "X-Client-Time-Almaty": now.isoformat(),
    }
    if CRM_AUTH_TYPE == "bearer" and CRM_API_TOKEN:
        headers["Authorization"] = f"Bearer {CRM_API_TOKEN}"
    return headers


def _crm_auth() -> aiohttp.BasicAuth | None:
    if CRM_AUTH_TYPE == "basic" and CRM_USERNAME:
        return aiohttp.BasicAuth(CRM_USERNAME, CRM_PASSWORD)
    return None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    async with _session_lock:
        if _session is None or _session.closed:
            _session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT, auth=_crm_auth())
        return _session


async def close_crm_session():
    global _session
    async with _session_lock:
        if _session is not None and not _session.closed:
            await _session.close()
            _session = None


def _normalize_doctor(raw: dict) -> dict:
    return {
        "id": str(raw.get("id") or raw.get("guid") or raw.get("name") or ""),
        "name": raw.get("name") or raw.get("full_name") or "Врач",
        "spec": raw.get("spec") or raw.get("specialty") or "Стоматолог",
    }


async def fetch_doctors_from_crm(page: int = 1, limit: int = 6):
    """Запрашивает из 1С/МИС список врачей с пагинацией и повтором при временных ошибках."""
    url = f"{CRM_BASE_URL}/api/doctors"
    params = {"page": page, "limit": limit, "timezone": ALMATY_TZ_NAME}
    max_attempts = 2

    try:
        session = await _get_session()
        for attempt in range(1, max_attempts + 1):
            async with session.get(url, params=params, headers=_crm_headers()) as response:
                if response.status == 200:
                    data = await response.json()
                    raw_docs = data.get("doctors") or data.get("items") or []
                    doctors = [_normalize_doctor(d) for d in raw_docs]
                    total = data.get("total", len(doctors))
                    return {"doctors": doctors, "total": total, "page": data.get("page", page)}
                if response.status in RETRY_STATUSES and attempt < max_attempts:
                    print(f"⚠️ 1С вернула {response.status} при получении врачей, повтор через 0.5s...")
                    await asyncio.sleep(0.5)
                    continue
                print(f"⚠️ Ошибка 1С при получении врачей: статус {response.status}")
                return {"doctors": [], "total": 0, "page": 1}
    except Exception as e:
        print(f"❌ Не удалось подключиться к 1С: {e}")
        return {"doctors": [], "total": 0, "page": 1}


async def fetch_available_slots_from_crm(doctor_name: str, doctor_id: str | None = None):
    """Запрашивает из 1С/МИС свободные слоты для врача по времени Алматы (GMT+5)."""
    url = f"{CRM_BASE_URL}/api/slots"
    almaty_date = now_almaty().strftime("%Y-%m-%d")
    params = {
        "doctor_name": doctor_name,
        "date": almaty_date,
        "timezone": ALMATY_TZ_NAME,
    }
    if doctor_id:
        params["doctor_id"] = doctor_id
    max_attempts = 2

    try:
        session = await _get_session()
        for attempt in range(1, max_attempts + 1):
            async with session.get(url, params=params, headers=_crm_headers()) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("available_slots") or data.get("slots") or []
                if response.status in RETRY_STATUSES and attempt < max_attempts:
                    print(f"⚠️ 1С вернула {response.status} при запросе слотов для {doctor_name}, повтор через 0.5s...")
                    await asyncio.sleep(0.5)
                    continue
                print(f"⚠️ Ошибка 1С при получении слотов для {doctor_name}: статус {response.status}")
                return []
    except Exception as e:
        print(f"❌ Ошибка соединения с 1С при запросе слотов: {e}")
        return []


async def sync_create_booking_in_crm(booking_data: dict) -> dict:
    """Отправляет созданную запись в 1С/МИС. Возвращает {'ok': bool, 'conflict': bool, 'error': str}."""
    url = f"{CRM_BASE_URL}/api/bookings"
    payload = {
        **booking_data,
        "source": "TelegramBot",
        "timezone": ALMATY_TZ_NAME,
        "utc_offset": ALMATY_UTC_OFFSET,
        "created_at_almaty": now_almaty().isoformat(),
    }

    try:
        session = await _get_session()
        async with session.post(url, json=payload, headers=_crm_headers()) as response:
            if response.status in (200, 201):
                res_json = await response.json()
                print(f"✅ Успешно синхронизировано с 1С (GMT+5): {res_json}")
                return {"ok": True, "conflict": False, "error": ""}
            if response.status == 409:
                return {"ok": False, "conflict": True, "error": "409 Conflict"}
            err_text = await response.text()
            print(f"⚠️ 1С отклонила запись (статус {response.status}): {err_text}")
            return {"ok": False, "conflict": False, "error": f"HTTP {response.status}"}
    except Exception as e:
        print(f"❌ Ошибка отправки брони в 1С: {e}")
        return {"ok": False, "conflict": False, "error": str(e)}


async def sync_cancel_booking_in_crm(doctor_name: str, time_slot: str) -> bool:
    """Уведомляет 1С/МИС об отмене записи и освобождении слота."""
    url = f"{CRM_BASE_URL}/api/bookings"
    params = {
        "doctor": doctor_name,
        "time": time_slot,
        "timezone": ALMATY_TZ_NAME,
    }

    try:
        session = await _get_session()
        async with session.delete(url, params=params, headers=_crm_headers()) as response:
            if response.status in (200, 204):
                print(f"🗑️ 1С подтвердила отмену записи: врач {doctor_name}, слот {time_slot}")
                return True
            print(f"⚠️ Ошибка 1С при отмене записи: статус {response.status}")
            return False
    except Exception as e:
        print(f"❌ Ошибка связи с 1С при отмене: {e}")
        return False


async def sync_reschedule_booking_in_crm(doctor_name: str, old_time_slot: str, new_time_slot: str) -> bool:
    """
    Синхронизирует перенос записи с 1С/МИС: пробует PATCH/PUT /api/bookings/reschedule,
    а если внешней CRM не поддерживает отдельный эндпоинт — освобождает старый слот и создаёт новый.
    """
    url = f"{CRM_BASE_URL}/api/bookings/reschedule"
    payload = {
        "doctor": doctor_name,
        "old_time": old_time_slot,
        "new_time": new_time_slot,
        "timezone": ALMATY_TZ_NAME,
        "utc_offset": ALMATY_UTC_OFFSET,
    }

    try:
        session = await _get_session()
        async with session.patch(url, json=payload, headers=_crm_headers()) as response:
            if response.status in (200, 204):
                return True
            if response.status not in (404, 405):
                return False
    except Exception:
        pass

    cancelled_old = await sync_cancel_booking_in_crm(doctor_name, old_time_slot)
    created_new = await sync_create_booking_in_crm({
        "doctor": doctor_name,
        "time": new_time_slot,
        "rescheduled_from": old_time_slot,
    })
    return cancelled_old and bool(created_new.get("ok"))