from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict
import uvicorn

app = FastAPI(title="Mock 1C / CRM Dental System", version="1.0")

# 🏥 Генерируем 50 врачей с распределением по специализациям
SPECIALTIES = ["Терапевт", "Ортодонт", "Хирург-имплантолог", "Ортопед", "Пародонтолог"]
DOCTORS_DB: Dict[str, dict] = {}

# Базовые слоты на день
DEFAULT_SLOTS = ["09:00", "10:30", "12:00", "14:00", "15:30", "17:00"]

for i in range(1, 51):
    doc_name = f"Врач #{i:02d} (Смирнов А.В.)" if i % 2 == 0 else f"Врач #{i:02d} (Иванова О.П.)"
    spec = SPECIALTIES[i % len(SPECIALTIES)]

    # Для каждого врача создаем слоты.
    # Структура: { "09:00": {"status": "free", "patient": None, "phone": None, "chat_id": None}, ... }
    slots = {
        slot: {"status": "free", "patient": None, "phone": None, "chat_id": None}
        for slot in DEFAULT_SLOTS
    }

    DOCTORS_DB[doc_name] = {
        "id": i,
        "name": doc_name,
        "spec": spec,
        "slots": slots
    }


class BookingRequest(BaseModel):
    doctor: str
    time: str
    patient: str
    phone: str
    user_id: int


@app.get("/api/doctors")
def get_doctors(page: int = 1, limit: int = 10):
    """Возвращает список врачей с пагинацией (по умолчанию по 10 на страницу)"""
    docs_list = list(DOCTORS_DB.values())
    start = (page - 1) * limit
    end = start + limit
    paginated = docs_list[start:end]

    return {
        "page": page,
        "limit": limit,
        "total": len(docs_list),
        "doctors": [{"name": d["name"], "spec": d["spec"]} for d in paginated]
    }


@app.get("/api/slots")
def get_slots_by_query(doctor_name: str):
    """Возвращает свободные слоты конкретного врача по query-параметру doctor_name"""
    return get_slots(doctor_name)


@app.get("/api/slots/{doctor_name}")
def get_slots(doctor_name: str):
    """Возвращает свободные слоты конкретного врача"""
    if doctor_name not in DOCTORS_DB:
        raise HTTPException(status_code=404, detail="Врач не найден в 1С")

    doc_data = DOCTORS_DB[doctor_name]
    # Возвращаем только те слоты, у которых статус free
    free_slots = [time for time, info in doc_data["slots"].items() if info["status"] == "free"]
    return {"doctor": doctor_name, "available_slots": free_slots}


@app.post("/api/bookings")
def create_booking(data: BookingRequest):
    """Создает запись в 1С: переводит слот в статус 'booked', сохраняет телефон и chat_id"""
    if data.doctor not in DOCTORS_DB:
        raise HTTPException(status_code=404, detail="Врач не найден")

    doc_slots = DOCTORS_DB[data.doctor]["slots"]
    if data.time not in doc_slots:
        raise HTTPException(status_code=400, detail="Слот не найден")

    if doc_slots[data.time]["status"] == "booked":
        raise HTTPException(status_code=409, detail="Слот уже занят в 1С!")

    # Бронируем в 1С
    doc_slots[data.time] = {
        "status": "booked",
        "patient": data.patient,
        "phone": data.phone,
        "chat_id": data.user_id
    }

    print(
        f"📦 [1С СИМУЛЯТОР] Успешная запись! Врач: {data.doctor}, Время: {data.time}, Пациент: {data.patient}, Тел: {data.phone}")
    return {"status": "success", "message": "Запись успешно зафиксирована в 1С"}


@app.delete("/api/bookings")
def cancel_booking(doctor: str, time: str):
    """Отменяет запись в 1С: освобождает слот"""
    if doctor not in DOCTORS_DB or time not in DOCTORS_DB[doctor]["slots"]:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    DOCTORS_DB[doctor]["slots"][time] = {
        "status": "free",
        "patient": None,
        "phone": None,
        "chat_id": None
    }
    print(f"🗑️ [1С СИМУЛЯТОР] Запись отменена. Врач: {doctor}, Слот {time} снова свободен.")
    return {"status": "success", "message": "Слот освобожден в 1С"}


@app.get("/api/admin/all_bookings")
def get_all_1c_bookings():
    """Эндпоинт для админа 1С: посмотреть все занятые записи по всем 50 врачам"""
    result = []
    for doc_name, doc_info in DOCTORS_DB.items():
        for time, info in doc_info["slots"].items():
            if info["status"] == "booked":
                result.append({
                    "doctor": doc_name,
                    "time": time,
                    "patient": info["patient"],
                    "phone": info["phone"],
                    "chat_id": info["chat_id"]
                })
    return {"total_bookings": len(result), "bookings": result}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8081)