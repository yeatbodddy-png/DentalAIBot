import aiohttp

# Адрес нашего локального симулятора 1С (который крутится на порту 8081)
CRM_BASE_URL = "http://127.0.0.1:8081"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=5)


async def fetch_doctors_from_crm(page: int = 1, limit: int = 6):
    """
    Запрашивает из 1С список врачей с пагинацией (по умолчанию по 6 врачей на страницу,
    чтобы не создавать простыню в Telegram).
    """
    url = f"{CRM_BASE_URL}/api/doctors"
    params = {"page": page, "limit": limit}

    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    print(f"⚠️ Ошибка 1С при получении врачей: статус {response.status}")
                    return {"doctors": [], "total": 0, "page": 1}
    except Exception as e:
        print(f"❌ Не удалось подключиться к 1С (сервер выключен?): {e}")
        return {"doctors": [], "total": 0, "page": 1}


async def fetch_available_slots_from_crm(doctor_name: str):
    """
    Запрашивает из 1С свободные слоты для конкретного врача через query-параметр.
    """
    url = f"{CRM_BASE_URL}/api/slots"
    params = {"doctor_name": doctor_name}

    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("available_slots", [])
                else:
                    print(f"⚠️ Ошибка 1С при получении слотов для {doctor_name}: статус {response.status}")
                    return []
    except Exception as e:
        print(f"❌ Ошибка соединения с 1С при запросе слотов: {e}")
        return []


async def sync_create_booking_in_crm(booking_data: dict):
    """
    Отправляет созданную запись в 1С (переводит слот в статус 'booked',
    сохраняет имя пациента, телефон и chat_id для связи).
    """
    url = f"{CRM_BASE_URL}/api/bookings"

    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.post(url, json=booking_data) as response:
                if response.status == 200:
                    res_json = await response.json()
                    print(f"✅ Успешно синхронизировано с 1С: {res_json}")
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
    params = {"doctor": doctor_name, "time": time_slot}

    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.delete(url, params=params) as response:
                if response.status == 200:
                    print(f"🗑️ 1С подтвердила отмену записи: врач {doctor_name}, слот {time_slot}")
                    return True
                else:
                    print(f"⚠️ Ошибка 1С при отмене записи: статус {response.status}")
                    return False
    except Exception as e:
        print(f"❌ Ошибка связи с 1С при отмене: {e}")
        return False