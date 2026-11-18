import aiohttp
import os

# Получаем настройки из .env (если подключим реальную CRM)
CRM_API_URL = os.getenv("CRM_API_URL", "https://api.yclients.com/api/v1")
CRM_TOKEN = os.getenv("CRM_TOKEN", "your_api_token_here")


async def fetch_available_slots_from_crm(doctor_name, date_str="today"):
    """
    Запрашивает актуальные свободные слоты у внешней CRM/МИС клиники.
    Если API еще не подключено, возвращает дефолтный актуальный список из базы данных / конфига.
    """
    # 💡 Здесь в будущем будет реальный GET-запрос к API YClients / 1С
    # async with aiohttp.ClientSession() as session:
    #     headers = {"Authorization": f"Bearer {CRM_TOKEN}"}
    #     async with session.get(f"{CRM_API_URL}/book_times/... ", headers=headers) as resp:
    #         if resp.status == 200:
    #             return await resp.json()

    # Пока API в разработке, возвращаем проверенный базовый набор слотов из нашей структуры
    from dental_data import DOCTORS
    for doc_id, doc_info in DOCTORS.items():
        if doc_info["name"] == doctor_name:
            return doc_info["slots"]
    return ["10:00", "12:00", "15:00", "17:00"]


async def sync_create_booking_in_crm(booking_data):
    """
    Отправляет данные о новой записи в CRM-систему клиники (создает визит и карточку пациента).
    """
    payload = {
        "user_id": booking_data.get("user_id"),
        "patient_name": booking_data.get("patient"),
        "phone": booking_data.get("phone"),
        "doctor": booking_data.get("doctor"),
        "time_slot": booking_data.get("time")
    }

    print(f"🔄 [CRM Sync] Отправка новой брони в систему клиники: {payload}")

    # 💡 Здесь в будущем будет реальный POST-запрос к API CRM
    # async with aiohttp.ClientSession() as session:
    #     async with session.post(f"{CRM_API_URL}/records", json=payload) as resp:
    #         return resp.status == 201

    return True


async def sync_cancel_booking_in_crm(doctor, time_slot):
    """
    Уведомляет CRM об отмене визита, чтобы слот снова стал доступен для других пациентов.
    """
    print(f"🗑️ [CRM Sync] Отмена визита в системе клиники: Врач {doctor}, слот {time_slot}")

    # 💡 Здесь будет DELETE-запрос к API CRM
    return True