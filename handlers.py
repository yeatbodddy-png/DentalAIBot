import os
import io
import asyncio
import re
import time
import tempfile
from datetime import timedelta
from config import (
    bot,
    client,
    MODES,
    user_histories,
    user_modes,
    ADMIN_IDS,
    CLINIC_NAME,
    CLINIC_CITY,
    CLINIC_ADDRESS,
)
from ai_service import ask_ai
from utils import get_keyboard, send_long_message, send_smart_reply, now_almaty_naive
from dental_data import CLINIC_INFO, SERVICES_TEXT
from database import (
    add_booking,
    remove_booking_by_id,
    release_slot_and_mark_status,
    reschedule_booking,
    is_slot_booked,
    get_all_bookings,
    get_user_bookings,
    mask_phone,
)
from analytics_service import (
    detect_service_topic,
    log_analytics_event,
    get_owner_dashboard,
    export_owner_csv_report,
)
from crm_service import (
    fetch_doctors_from_crm,
    fetch_available_slots_from_crm,
    sync_create_booking_in_crm,
    sync_cancel_booking_in_crm,
    sync_reschedule_booking_in_crm,
)
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

appointments_storage = {}
_user_rate_timestamps: dict[int, list[float]] = {}

DOCTORS_PER_PAGE = 6
RATE_LIMIT_MAX_EVENTS = 12
RATE_LIMIT_WINDOW_SEC = 60.0

MENU_BUTTONS = {
    "👩‍⚕️ Администратор",
    "🦷 Врач-консультант",
    "🩺 Услуги",
    "📅 Записаться",
    "📋 Мои записи",
    "📍 Адрес",
    "🕒 График работы",
    "🔄 Сменить роль",
}

_DENTAL_AND_CHAT_ROOTS = (
    "здрав", "привет", "салам", "подскаж", "скаж", "скольк", "цена", "стои", "прайс",
    "зуб", "кариес", "пломб", "канал", "удален", "мудрост", "имплант", "коронк",
    "винир", "брекет", "чистк", "болит", "боль", "лечен", "врач", "доктор",
    "запис", "свобод", "время", "адрес", "график", "работа", "скидк", "акци", "телефон",
    "сәлем", "салем", "қайырлы", "сұрақ", "баға", "қанша", "тұрад", "тіс", "емде",
    "түбір", "арна", "жұл", "алу", "қаптама", "винір", "ауыр", "дәрігер",
    "жазыл", "бос", "уақыт", "мекенжай", "жұмыс", "жеңілдік", "нөмір", "хабарлас",
)

_WHISPER_HALLUCINATIONS = (
    "субтитр", "продолжение следует", "подписывайтесь", "спасибо за просмотр",
    "dimatorzok", "amara.org", "thanks for watching",
)


def _check_rate_limit(chat_id: int) -> bool:
    """Возвращает True, если пользователь укладывается в лимит запросов в минуту."""
    if chat_id in ADMIN_IDS:
        return True
    now = time.monotonic()
    history = [ts for ts in _user_rate_timestamps.get(chat_id, []) if now - ts <= RATE_LIMIT_WINDOW_SEC]
    if len(history) >= RATE_LIMIT_MAX_EVENTS:
        _user_rate_timestamps[chat_id] = history
        return False
    history.append(now)
    _user_rate_timestamps[chat_id] = history
    return True


def _slot_to_almaty_dt(time_slot: str) -> str | None:
    try:
        hour, minute = map(int, time_slot.split(":"))
        now = now_almaty_naive()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def _is_garbled_voice(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True

    if any(bad in low for bad in _WHISPER_HALLUCINATIONS):
        return True

    words = re.findall(r"[а-яёәіңғүұқөһa-z]+", low)
    if not words:
        return True

    if len(words) >= 3 and not any(root in low for root in _DENTAL_AND_CHAT_ROOTS):
        return True

    return False


async def notify_all_admins(text, parse_mode="HTML"):
    if not ADMIN_IDS:
        print("⚠️ Предупреждение: список ADMIN_IDS пуст, некому отправлять уведомления!")
        return

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=parse_mode)
        except Exception as e:
            print(f"⚠️ Не удалось отправить уведомление администратору {admin_id}: {e}")


async def check_mode_selected(message):
    chat_id = message.chat.id
    if chat_id not in user_modes or user_modes[chat_id] is None:
        await bot.reply_to(
            message,
            "🛑 Пожалуйста, сначала выберите роль для общения с помощью кнопок снизу 👇",
            reply_markup=get_keyboard()
        )
        return False
    return True


async def build_doctors_keyboard(chat_id: int, page: int = 1, limit: int = DOCTORS_PER_PAGE):
    data = await fetch_doctors_from_crm(page=max(1, page), limit=limit)
    doctors = data.get("doctors", [])
    total = data.get("total", 0)
    current_page = data.get("page", page)

    if not doctors and total == 0:
        return (
            "⚠️ Не удалось получить список врачей из 1С. Проверьте, запущен ли симулятор 1С (`mock_1c_server.py`).",
            None,
        )

    total_pages = max(1, (total + limit - 1) // limit)
    current_page = min(max(1, current_page), total_pages)

    if chat_id not in appointments_storage:
        appointments_storage[chat_id] = {}
    appointments_storage[chat_id]["page"] = current_page
    appointments_storage[chat_id]["docs_on_page"] = {
        str(idx): doc for idx, doc in enumerate(doctors)
    }

    markup = InlineKeyboardMarkup(row_width=1)
    for idx, doc in enumerate(doctors):
        btn_text = f"{doc['name']} ({doc['spec']})"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"doc_{current_page}_{idx}"))

    nav_buttons = []
    if current_page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"docs_page_{current_page - 1}"))
    if current_page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"docs_page_{current_page + 1}"))
    if nav_buttons:
        markup.row(*nav_buttons)

    text = (
        f"🗓 <b>Онлайн-запись в клинику ({CLINIC_CITY})</b>\n"
        f"Страница <b>{current_page}</b> из <b>{total_pages}</b> (всего врачей: {total})\n\n"
        "Выберите врача, к которому хотите записаться:"
    )
    return text, markup


@bot.message_handler(commands=["start"])
async def handle_start(message):
    chat_id = message.chat.id
    user_modes[chat_id] = None
    await log_analytics_event(chat_id, "bot_start")

    await bot.send_chat_action(chat_id, "typing")

    welcome_text = (
        f"👋 Здравствуйте! Добро пожаловать в клинику <b>«{CLINIC_NAME}»</b> ({CLINIC_CITY}).\n\n"
        "Я ваш цифровой помощник. Сначала выберите, с кем вы хотите пообщаться:\n"
        "• <b>👩‍⚕️ Администратор</b> — ответит на вопросы по ценам, расчетам услуг и записи.\n"
        "• <b>🦷 Врач-консультант</b> — проконсультирует по медицинским вопросам и стоимости лечения.\n\n"
        "Выберите роль с помощью кнопок на клавиатуре снизу 👇"
    )
    await bot.send_message(chat_id, welcome_text, parse_mode="HTML", reply_markup=get_keyboard())


@bot.message_handler(commands=["reset"])
async def handle_reset(message):
    chat_id = message.chat.id
    current_mode = user_modes.get(chat_id, "👩‍⚕️ Администратор")

    if chat_id in user_histories and current_mode in user_histories[chat_id]:
        user_histories[chat_id][current_mode] = []

    await bot.reply_to(
        message,
        f"🧹 Память режима *{current_mode}* очищена! Начинаем диалог с чистого листа.",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["admin"])
async def handle_admin_panel(message):
    chat_id = message.chat.id

    if chat_id not in ADMIN_IDS:
        await bot.reply_to(message, "⛔ У вас нет доступа к панели администратора.")
        return

    active_bookings = await get_all_bookings()

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📊 Аналитика и CSV-отчёт", callback_data="adm_analytics"))

    if not active_bookings:
        await bot.send_message(
            chat_id,
            "📋 <b>Панель управления ресепшена:</b>\n\nВ данный момент активных записей нет.",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    text = "📋 <b>Панель ресепшена: Активные записи пациентов</b>\n\nНажмите на запись, чтобы отменить её и освободить слот:\n"

    for booking_id, booking in active_bookings.items():
        doctor = booking["doctor"]
        time_slot = booking["time"]
        patient_info = booking.get("patient", "Пациент")
        phone = booking.get("phone", "Не указан")
        anamnesis = booking.get("anamnesis", "Не указан")

        text += (
            f"\n🆔 [Запись #{booking_id}] 👤 <b>{patient_info}</b>\n"
            f"📱 Тел: <b>{phone}</b> | 🩺 {anamnesis}\n"
            f"👨‍⚕️ {doctor} 🕒 <b>{time_slot}</b>\n"
        )

        btn_text = f"❌ Отменить (#{booking_id}): {patient_info[:8]}... ({time_slot})"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"adm_cancel_{booking_id}"))

    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


def _format_owner_report(stats: dict) -> str:
    days = stats.get("days")
    period_title = f"за последние {days} дн." if days else "за всё время"
    top_srv = "\n".join(f"• {s}: {c}" for s, c in stats["top_services"]) or "• Пока нет данных"
    top_doc = "\n".join(f"• {d}: {c}" for d, c in stats["top_doctors"]) or "• Пока нет данных"

    return (
        f"📊 <b>Аналитика клиники «{CLINIC_NAME}» ({period_title})</b>\n\n"
        "<b>1. Воронка привлечения:</b>\n"
        f"• Запустили бота (/start): <b>{stats['unique_starts']}</b>\n"
        f"• Вопросы по услугам/ценам: <b>{stats['price_questions']}</b>\n"
        f"• Открыли запись: <b>{stats['booking_starts']}</b>\n"
        f"• Выбрали врача и слот: <b>{stats['slots_selected']}</b>\n"
        f"• Завершили запись: <b>{stats['completed']}</b>\n"
        f"• Отвалились на этапе телефона: <b>{stats['phone_dropoff']}</b>\n"
        f"• Конверсия «слот → запись»: <b>{stats['slot_to_book_conv']}%</b>\n"
        f"• Конверсия «вопрос о цене → запись»: <b>{stats['ask_to_book_conv']}%</b>\n\n"
        "<b>2. Статусы записей:</b>\n"
        f"• Активные: <b>{stats['active']}</b> | Перенесены: <b>{stats['rescheduled']}</b> | Отменены: <b>{stats['cancelled']}</b>\n"
        f"• Обращения с острой болью: <b>{stats['acute_pain']}</b>\n\n"
        f"<b>3. Топ запрашиваемых услуг:</b>\n{top_srv}\n\n"
        f"<b>4. Топ врачей по записям:</b>\n{top_doc}"
    )


def _analytics_period_kb(selected_days: int | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("7 дней", callback_data="adm_stats_7"),
        InlineKeyboardButton("30 дней", callback_data="adm_stats_30"),
        InlineKeyboardButton("Всё время", callback_data="adm_stats_all"),
    )
    csv_tag = str(selected_days) if selected_days else "all"
    kb.add(InlineKeyboardButton("📥 Скачать CSV для Excel", callback_data=f"adm_csv_{csv_tag}"))
    return kb


@bot.callback_query_handler(func=lambda call: call.data == "adm_analytics" or call.data.startswith("adm_stats_"))
async def callback_admin_analytics(call):
    if call.message.chat.id not in ADMIN_IDS:
        await bot.answer_callback_query(call.id, "⛔ Недостаточно прав.", show_alert=True)
        return

    days: int | None = 30
    if call.data == "adm_stats_7":
        days = 7
    elif call.data == "adm_stats_30":
        days = 30
    elif call.data == "adm_stats_all":
        days = None

    stats = await get_owner_dashboard(days=days)
    text = _format_owner_report(stats)
    kb = _analytics_period_kb(days)

    if call.data == "adm_analytics":
        await bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=kb)
    else:
        try:
            await bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception:
            pass
    await bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_csv_"))
async def callback_admin_csv_export(call):
    if call.message.chat.id not in ADMIN_IDS:
        await bot.answer_callback_query(call.id, "⛔ Недостаточно прав.", show_alert=True)
        return

    tag = call.data.replace("adm_csv_", "", 1)
    days = int(tag) if tag.isdigit() else None

    csv_bytes = await export_owner_csv_report(days=days)
    csv_file = io.BytesIO(csv_bytes)
    csv_file.name = f"dentalbot_report_{tag}.csv"

    await bot.send_document(
        call.message.chat.id,
        csv_file,
        caption="📎 Готовый отчёт для Excel (разделитель `;`, кодировка UTF-8 BOM)",
    )
    await bot.answer_callback_query(call.id, "✅ Отчёт сформирован")


@bot.message_handler(func=lambda msg: msg.text in ["👩‍⚕️ Администратор", "🦷 Врач-консультант"])
async def handle_mode_switch(message):
    chat_id = message.chat.id
    user_modes[chat_id] = message.text

    menu_markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    menu_markup.add(
        KeyboardButton("🩺 Услуги"),
        KeyboardButton("📅 Записаться"),
        KeyboardButton("📋 Мои записи"),
        KeyboardButton("📍 Адрес"),
        KeyboardButton("🕒 График работы"),
        KeyboardButton("🔄 Сменить роль")
    )

    await bot.reply_to(
        message,
        f"✅ Режим успешно установлен: <b>{message.text}</b>.\n\nТеперь вы можете выбрать интересующий вас раздел из меню ниже или задать любой вопрос в чате! 👇",
        parse_mode="HTML",
        reply_markup=menu_markup
    )


@bot.message_handler(func=lambda msg: msg.text == "📋 Мои записи")
async def handle_my_appointments(message):
    if not await check_mode_selected(message):
        return

    chat_id = message.chat.id
    user_bookings = await get_user_bookings(chat_id)

    if not user_bookings:
        await bot.reply_to(
            message,
            "📭 У вас пока нет активных записей на прием.\n\nВы можете записаться через кнопку «📅 Записаться» в меню.",
            parse_mode="HTML"
        )
        return

    for idx, (booking_id, doctor, time_slot, appointment_dt, phone, anamnesis) in enumerate(user_bookings, 1):
        time_display = appointment_dt or time_slot
        card_text = (
            f"📌 <b>Запись #{idx}</b>\n"
            f"👨‍⚕️ Врач: <b>{doctor}</b>\n"
            f"🕒 Время: <b>{time_display}</b>\n"
            f"🩺 Триаж: {anamnesis}\n"
            f"📱 Телефон: <b>{phone}</b>"
        )
        card_kb = InlineKeyboardMarkup(row_width=2)
        card_kb.add(
            InlineKeyboardButton("🔄 Перенести", callback_data=f"pat_resched_{booking_id}"),
            InlineKeyboardButton("❌ Отменить", callback_data=f"pat_cancel_{booking_id}"),
        )
        await bot.send_message(chat_id, card_text, parse_mode="HTML", reply_markup=card_kb)


@bot.callback_query_handler(func=lambda call: call.data.startswith("pat_cancel_"))
async def callback_patient_cancel(call):
    chat_id = call.message.chat.id
    if not _check_rate_limit(chat_id):
        await bot.answer_callback_query(call.id, "⏳ Слишком много запросов. Подождите минуту.", show_alert=True)
        return

    try:
        booking_id = int(call.data.replace("pat_cancel_", ""))
    except ValueError:
        await bot.answer_callback_query(call.id, "⚠️ Некорректный запрос.", show_alert=True)
        return

    released = await release_slot_and_mark_status(booking_id, new_status="cancelled")
    if not released or released["user_id"] != chat_id:
        await bot.answer_callback_query(call.id, "⚠️ Запись уже отменена или не найдена.", show_alert=True)
        return

    await sync_cancel_booking_in_crm(released["doctor"], released["time"])
    await notify_all_admins(
        f"ℹ️ <b>Пациент отменил запись</b>\n\n"
        f"👨‍⚕️ Врач: <b>{released['doctor']}</b>\n"
        f"🕒 Слот освобождён: <b>{released['time']}</b>"
    )

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"❌ Запись к врачу <b>{released['doctor']}</b> на <b>{released['time']}</b> отменена. Слот освобождён.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await bot.answer_callback_query(call.id, "✅ Запись отменена")


@bot.callback_query_handler(func=lambda call: call.data.startswith("pat_resched_"))
async def callback_patient_reschedule_start(call):
    chat_id = call.message.chat.id
    if not _check_rate_limit(chat_id):
        await bot.answer_callback_query(call.id, "⏳ Слишком много запросов. Подождите минуту.", show_alert=True)
        return

    try:
        booking_id = int(call.data.replace("pat_resched_", ""))
    except ValueError:
        await bot.answer_callback_query(call.id, "⚠️ Некорректный запрос.", show_alert=True)
        return

    user_bookings = await get_user_bookings(chat_id)
    target_booking = next((b for b in user_bookings if b[0] == booking_id), None)
    if not target_booking:
        await bot.answer_callback_query(call.id, "⚠️ Запись не найдена.", show_alert=True)
        return

    _, doctor, current_slot, _, _, _ = target_booking
    live_slots = await fetch_available_slots_from_crm(doctor)

    markup = InlineKeyboardMarkup(row_width=2)
    found = False
    for slot in live_slots:
        if slot == current_slot or not await is_slot_booked(doctor, slot):
            found = True
            markup.add(InlineKeyboardButton(f"🕒 {slot}", callback_data=f"do_resched_{booking_id}_{slot}"))

    if not found:
        await bot.answer_callback_query(call.id, "⚠️ Нет других свободных слотов у этого врача.", show_alert=True)
        return

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🔄 <b>Перенос записи</b>\nВрач: <b>{doctor}</b> (текущее время: <b>{current_slot}</b>)\n\nВыберите новое время:",
        parse_mode="HTML",
        reply_markup=markup,
    )
    await bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("do_resched_"))
async def callback_patient_reschedule_apply(call):
    chat_id = call.message.chat.id
    parts = call.data.replace("do_resched_", "", 1).split("_", 1)
    if len(parts) != 2:
        await bot.answer_callback_query(call.id, "⚠️ Ошибка переноса.", show_alert=True)
        return

    try:
        booking_id = int(parts[0])
    except ValueError:
        await bot.answer_callback_query(call.id, "⚠️ Ошибка переноса.", show_alert=True)
        return

    new_slot = parts[1]
    new_dt = _slot_to_almaty_dt(new_slot)
    res = await reschedule_booking(booking_id, chat_id, new_slot, new_dt)
    if not res:
        await bot.answer_callback_query(call.id, "⚠️ Этот слот уже заняли. Выберите другое время.", show_alert=True)
        return

    await sync_reschedule_booking_in_crm(res["doctor"], res["old_time"], res["new_time"])
    await notify_all_admins(
        f"🔄 <b>Пациент перенёс запись</b>\n\n"
        f"👨‍⚕️ Врач: <b>{res['doctor']}</b>\n"
        f"🕒 Было: <b>{res['old_time']}</b> → Стало: <b>{res['new_time']}</b>"
    )

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=(
                f"✅ <b>Запись перенесена!</b>\n"
                f"👨‍⚕️ Врач: <b>{res['doctor']}</b>\n"
                f"🕒 Новое время: <b>{new_dt or new_slot}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await bot.answer_callback_query(call.id, "✅ Запись перенесена")


@bot.message_handler(func=lambda msg: msg.text == "🔄 Сменить роль")
async def handle_change_role(message):
    chat_id = message.chat.id
    user_modes[chat_id] = None
    await bot.reply_to(
        message,
        "Выберите желаемую роль:",
        reply_markup=get_keyboard()
    )


@bot.message_handler(func=lambda msg: msg.text == "📍 Адрес")
async def handle_address(message):
    if not await check_mode_selected(message):
        return
    await bot.reply_to(message, f"{CLINIC_INFO['name']}\n\n{CLINIC_INFO['address']}\n{CLINIC_INFO['phone']}")


@bot.message_handler(func=lambda msg: msg.text == "🕒 График работы")
async def handle_work_hours(message):
    if not await check_mode_selected(message):
        return
    await bot.reply_to(message, CLINIC_INFO['work_hours'])


@bot.message_handler(func=lambda msg: msg.text == "🩺 Услуги")
async def handle_services(message):
    if not await check_mode_selected(message):
        return
    chat_id = message.chat.id
    await bot.send_chat_action(chat_id, "typing")

    photo_path = "photos/price_menu.webp"
    if os.path.exists(photo_path):
        try:
            with open(photo_path, "rb") as photo_file:
                await bot.send_photo(
                    chat_id,
                    photo_file,
                    caption="📋 <b>Наши услуги и актуальный прайс-лист (₸):</b>",
                    parse_mode="HTML"
                )
        except Exception as e:
            print(f"⚠️ Не удалось отправить фото: {e}")

    await send_long_message(message, SERVICES_TEXT)


@bot.message_handler(func=lambda msg: msg.text == "📅 Записаться")
async def handle_book_start(message):
    if not await check_mode_selected(message):
        return

    chat_id = message.chat.id
    if not _check_rate_limit(chat_id):
        await bot.reply_to(message, "⏳ Слишком много запросов. Подождите минуту.")
        return

    await log_analytics_event(chat_id, "booking_started")
    text, markup = await build_doctors_keyboard(chat_id=chat_id, page=1, limit=DOCTORS_PER_PAGE)
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("docs_page_"))
async def callback_docs_page(call):
    chat_id = call.message.chat.id
    try:
        page = int(call.data.replace("docs_page_", ""))
    except ValueError:
        page = 1

    text, markup = await build_doctors_keyboard(chat_id=chat_id, page=page, limit=DOCTORS_PER_PAGE)
    if not markup:
        await bot.answer_callback_query(call.id, "⚠️ 1С недоступна", show_alert=True)
        return

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception:
        pass
    await bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("doc_"))
async def callback_choose_doctor(call):
    chat_id = call.message.chat.id
    parts = call.data.split("_")
    page = int(parts[1]) if len(parts) >= 3 else 1
    doc_idx = parts[2] if len(parts) >= 3 else "0"

    user_state = appointments_storage.get(chat_id, {})
    docs_on_page = user_state.get("docs_on_page")

    if not docs_on_page:
        data = await fetch_doctors_from_crm(page=page, limit=DOCTORS_PER_PAGE)
        docs_list = data.get("doctors", [])
        docs_on_page = {str(i): d for i, d in enumerate(docs_list)}

    doc = docs_on_page.get(str(doc_idx))
    if not doc:
        await bot.answer_callback_query(call.id, "❌ Врач не найден. Обновите список.", show_alert=True)
        return

    appointments_storage[chat_id] = {
        **user_state,
        "page": page,
        "doctor": doc["name"],
        "spec": doc["spec"],
    }

    markup = InlineKeyboardMarkup(row_width=2)
    live_slots = await fetch_available_slots_from_crm(doc["name"])

    available_slots_found = False
    for time_slot in live_slots:
        if await is_slot_booked(doc["name"], time_slot):
            continue

        available_slots_found = True
        markup.add(InlineKeyboardButton(f"🕒 {time_slot}", callback_data=f"time_{time_slot}"))

    markup.add(InlineKeyboardButton("⬅️ Назад к списку врачей", callback_data="back_to_docs"))

    text = f"Вы выбрали: <b>{doc['name']}</b>\nСпециализация: {doc['spec']}\n\n"
    if available_slots_found:
        text += "Выберите свободное время для приема:"
    else:
        text += "⚠️ У этого врача пока нет свободных мест на ближайшее время."

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )
    await bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("time_"))
async def callback_choose_time(call):
    chat_id = call.message.chat.id
    time_slot = call.data.replace("time_", "", 1)

    user_state = appointments_storage.get(chat_id, {})
    doc_name = user_state.get("doctor")
    if not doc_name:
        await bot.answer_callback_query(call.id, "⚠️ Выберите врача заново.", show_alert=True)
        return

    if await is_slot_booked(doc_name, time_slot):
        await bot.answer_callback_query(call.id, "⚠️ Упс! Это время только что заняли. Выберите другое.", show_alert=True)
        return

    appointment_dt = _slot_to_almaty_dt(time_slot)
    appointments_storage[chat_id] = {
        **user_state,
        "time": time_slot,
        "appointment_dt": appointment_dt,
        "step": "triage_reason",
    }
    await log_analytics_event(chat_id, "slot_selected", meta=f"{doc_name} {time_slot}")

    triage_kb = InlineKeyboardMarkup(row_width=1)
    triage_kb.add(
        InlineKeyboardButton("🚨 Острая боль / срочно", callback_data="triage_reason_Острая боль"),
        InlineKeyboardButton("🦷 Лечение / плановый осмотр", callback_data="triage_reason_Плановый осмотр/лечение"),
        InlineKeyboardButton("💬 Консультация / смета", callback_data="triage_reason_Консультация"),
    )

    await bot.send_message(
        chat_id,
        f"✅ Вы выбрали: <b>{doc_name}</b> на время <b>{time_slot}</b>.\n\n"
        "<b>Шаг 1 из 2 (короткий анамнез):</b> какая цель визита?",
        parse_mode="HTML",
        reply_markup=triage_kb,
    )
    await bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("triage_reason_"))
async def callback_triage_reason(call):
    chat_id = call.message.chat.id
    reason = call.data.replace("triage_reason_", "", 1)
    state = appointments_storage.get(chat_id)
    if not state:
        await bot.answer_callback_query(call.id, "⚠️ Начните запись заново.", show_alert=True)
        return

    state["triage_reason"] = reason
    state["step"] = "triage_notes"

    notes_kb = InlineKeyboardMarkup(row_width=2)
    notes_kb.add(
        InlineKeyboardButton("Пропустить", callback_data="triage_notes_skip"),
        InlineKeyboardButton("Есть аллергии/особенности", callback_data="triage_notes_ask"),
    )

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=(
            f"Причина: <b>{reason}</b>.\n\n"
            "<b>Шаг 2 из 2:</b> есть ли у вас аллергии на лекарства, беременность или важные особенности? "
            "Нажмите кнопку ниже или напишите кратко текстом."
        ),
        parse_mode="HTML",
        reply_markup=notes_kb,
    )
    await bot.answer_callback_query(call.id)


async def _prompt_phone_with_consent(chat_id: int, anamnesis_summary: str):
    state = appointments_storage.get(chat_id, {})
    state["anamnesis"] = anamnesis_summary
    state["step"] = "phone"
    appointments_storage[chat_id] = state

    contact_markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    contact_btn = KeyboardButton("📱 Согласен на обработку ПДн и отправить телефон", request_contact=True)
    contact_markup.add(contact_btn)

    await bot.send_message(
        chat_id,
        f"🩺 Анамнез зафиксирован: <b>{anamnesis_summary}</b>.\n\n"
        "Последний шаг — отправьте номер телефона кнопкой ниже или цифрами.\n"
        "🔒 <i>Нажимая кнопку или отправляя номер, вы даёте согласие на обработку персональных данных "
        f"в клинике «{CLINIC_NAME}» для записи на приём и напоминаний.</i>",
        parse_mode="HTML",
        reply_markup=contact_markup,
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("triage_notes_"))
async def callback_triage_notes(call):
    chat_id = call.message.chat.id
    action = call.data.replace("triage_notes_", "", 1)
    state = appointments_storage.get(chat_id)
    if not state:
        await bot.answer_callback_query(call.id, "⚠️ Начните запись заново.", show_alert=True)
        return

    reason = state.get("triage_reason", "Приём")
    await bot.answer_callback_query(call.id)

    if action == "skip":
        await _prompt_phone_with_consent(chat_id, f"{reason}; особенности не указаны")
    else:
        state["step"] = "triage_notes_text"
        await bot.send_message(
            chat_id,
            "✍️ Напишите кратко об аллергиях, лекарствах или важных особенностях (например: <i>аллергия на лидокаин</i>):",
            parse_mode="HTML",
        )


@bot.message_handler(
    content_types=["contact", "text"],
    func=lambda msg: (
        msg.content_type == "contact"
        or (
            msg.chat.id in appointments_storage
            and "doctor" in appointments_storage[msg.chat.id]
            and "time" in appointments_storage[msg.chat.id]
        )
    )
)
async def handle_booking_step_input(message):
    chat_id = message.chat.id
    if not _check_rate_limit(chat_id):
        await bot.reply_to(message, "⏳ Слишком много запросов. Подождите минуту.")
        return

    state = appointments_storage.get(chat_id)
    if not state or "doctor" not in state or "time" not in state:
        return

    step = state.get("step", "phone")

    if step == "triage_notes_text" and message.content_type == "text" and message.text:
        reason = state.get("triage_reason", "Приём")
        notes = message.text.strip()[:180]
        await _prompt_phone_with_consent(chat_id, f"{reason}; {notes}")
        return

    doc_name = state["doctor"]
    time_slot = state["time"]
    appointment_dt = state.get("appointment_dt")
    anamnesis_summary = state.get("anamnesis", "Не указан")

    if message.contact:
        phone = message.contact.phone_number
    elif message.text and any(char.isdigit() for char in message.text):
        phone = message.text.strip()
    else:
        await bot.reply_to(message, "⚠️ Пожалуйста, отправьте номер телефона кнопкой ниже или напишите его цифрами.")
        return

    user = message.from_user
    user_fullname = user.first_name or "Без имени"
    user_display = f"{user_fullname} (@{user.username})" if user.username else f"{user_fullname} (ID: {user.id})"

    if await is_slot_booked(doc_name, time_slot):
        await bot.reply_to(message, "⚠️ К сожалению, это время только что занял другой пациент. Нажмите «📅 Записаться» заново.")
        appointments_storage.pop(chat_id, None)
        return

    booking_id = await add_booking(
        chat_id,
        doc_name,
        time_slot,
        user_display,
        phone,
        appointment_dt=appointment_dt,
        anamnesis=anamnesis_summary,
    )
    if not booking_id:
        await bot.reply_to(message, "⚠️ Не удалось закрепить слот. Попробуйте выбрать другое время.")
        appointments_storage.pop(chat_id, None)
        return

    await log_analytics_event(chat_id, "booking_completed", meta=doc_name)

    crm_res = await sync_create_booking_in_crm({
        "booking_id": booking_id,
        "user_id": chat_id,
        "patient": user_display,
        "phone": phone,
        "doctor": doc_name,
        "time": time_slot,
        "anamnesis": anamnesis_summary,
    })
    if crm_res.get("conflict"):
        await release_slot_and_mark_status(booking_id, new_status="cancelled")
        await bot.reply_to(message, "⚠️ В расписании 1С этот слот уже заняли. Выберите другое время через «📅 Записаться».")
        appointments_storage.pop(chat_id, None)
        return

    if not crm_res.get("ok"):
        print(f"⚠️ Запись #{booking_id} ({doc_name}, {time_slot}, тел. {mask_phone(phone)}) сохранена локально, 1С не подтвердила.")

    appointments_storage.pop(chat_id, None)

    menu_markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    menu_markup.add(
        KeyboardButton("🩺 Услуги"),
        KeyboardButton("📅 Записаться"),
        KeyboardButton("📋 Мои записи"),
        KeyboardButton("📍 Адрес"),
        KeyboardButton("🕒 График работы"),
        KeyboardButton("🔄 Сменить роль")
    )

    admin_notification_text = (
        "🚨 <b>Новая онлайн-запись в клинику!</b>\n\n"
        f"👤 Пациент: <b>{user_display}</b>\n"
        f"📱 Телефон: <b>+{phone if not phone.startswith('+') else phone}</b>\n"
        f"👨‍⚕️ Врач: <b>{doc_name}</b>\n"
        f"🕒 Время: <b>{time_slot}</b>\n"
        f"🩺 Анамнез: <b>{anamnesis_summary}</b>"
    )
    await notify_all_admins(admin_notification_text)

    await bot.send_message(
        chat_id,
        "✅ <b>Запись успешно оформлена! Ждем вас в клинике.</b>\n\n"
        f"📍 Адрес: {CLINIC_CITY}, {CLINIC_ADDRESS}.\n"
        f"👨‍⚕️ Врач: <b>{doc_name}</b>\n"
        f"🕒 Время: <b>{time_slot}</b>\n"
        f"🩺 Анамнез: <b>{anamnesis_summary}</b>\n"
        f"📱 Ваш телефон: <b>{phone}</b>\n\n"
        "При необходимости перенести или отменить визит зайдите в раздел «📋 Мои записи».",
        parse_mode="HTML",
        reply_markup=menu_markup
    )


@bot.callback_query_handler(func=lambda call: call.data == "back_to_docs")
async def callback_back_to_docs(call):
    chat_id = call.message.chat.id
    saved_page = appointments_storage.get(chat_id, {}).get("page", 1)
    text, markup = await build_doctors_keyboard(chat_id=chat_id, page=saved_page, limit=DOCTORS_PER_PAGE)

    if not markup:
        await bot.answer_callback_query(call.id, "⚠️ 1С недоступна", show_alert=True)
        return

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception:
        pass
    await bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_cancel_"))
async def callback_admin_cancel(call):
    admin_chat_id = call.message.chat.id

    if admin_chat_id not in ADMIN_IDS:
        await bot.answer_callback_query(call.id, "⛔ Недостаточно прав.", show_alert=True)
        return

    booking_id = int(call.data.replace("adm_cancel_", ""))

    active_bookings = await get_all_bookings()
    booking_info = active_bookings.get(booking_id)

    removed_data = await remove_booking_by_id(booking_id)

    if not removed_data or not booking_info:
        await bot.answer_callback_query(call.id, "⚠️ Эта запись уже была удалена или не существует.", show_alert=True)
        return

    doctor = removed_data["doctor"]
    time_slot = removed_data["time"]
    patient_chat_id = booking_info["user_id"]

    await sync_cancel_booking_in_crm(doctor, time_slot)

    await bot.answer_callback_query(call.id, "✅ Запись отменена, слот снова свободен!")

    try:
        patient_notification = (
            "❌ <b>К сожалению, ваша запись была отменена администратором клиники.</b>\n\n"
            f"👨‍⚕️ Врач: <b>{doctor}</b>\n"
            f"🕒 Время: <b>{time_slot}</b>\n\n"
            "Если у вас возникли вопросы вы можете позвонить Нам, либо если вы хотите перенести запись на другое время, вы можете выбрать нового врача через кнопку «📅 Записаться» в меню."
        )
        await bot.send_message(patient_chat_id, patient_notification, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Не удалось отправить уведомление пациенту {patient_chat_id}: {e}")

    await bot.edit_message_text(
        chat_id=admin_chat_id,
        message_id=call.message.message_id,
        text=f"✅ Запись #{booking_id} к врачу <b>{doctor}</b> на <b>{time_slot}</b> успешно отменена. Пациенту отправлено уведомление, слот освобожден.",
        parse_mode="HTML"
    )


@bot.message_handler(content_types=["voice"])
async def handle_voice(message):
    if not await check_mode_selected(message):
        return

    chat_id = message.chat.id
    if not _check_rate_limit(chat_id):
        await bot.reply_to(message, "⏳ Слишком много запросов. Подождите минуту.")
        return

    await bot.send_chat_action(chat_id, "typing")
    temp_path = None
    loop = asyncio.get_running_loop()

    try:
        file_info = await bot.get_file(message.voice.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
            temp_audio.write(downloaded_file)
            temp_path = temp_audio.name

        with open(temp_path, "rb") as audio_file:
            audio_bytes = audio_file.read()
            audio_filename = os.path.basename(temp_path)

        transcription = await loop.run_in_executor(
            None,
            lambda: client.audio.transcriptions.create(
                file=(audio_filename, audio_bytes),
                model="whisper-large-v3-turbo",
                temperature=0.0,
                prompt=(
                    f"Стоматологическая клиника в городе {CLINIC_CITY}. Русский и казахский языки: "
                    "тіс емдеу, кариес, пломба, канал, имплант, қаптама, винир, брекет, "
                    "бағасы қанша, мекенжай, жазылу."
                )
            )
        )

        recognized_text = (transcription.text or "").strip()
        print(f"🎙️ Whisper распознал для {chat_id}: {recognized_text!r}")

        if not recognized_text or _is_garbled_voice(recognized_text):
            await bot.reply_to(
                message,
                "🙏 Извините, в голосовом сообщении не всё удалось разобрать чётко (особенно при переходе с русского на казахский).\n"
                "Напишите, пожалуйста, ваш вопрос текстом — с радостью подскажу!\n\n"
                "Кешіріңіз, дауыстық хабарлама толық анық естілмеді. Сұрағыңызды мәтінмен жазып жіберіңізші — қуана жауап беремін! 😊"
            )
            return

        topic = detect_service_topic(recognized_text)
        if topic:
            await log_analytics_event(chat_id, "ai_price_question", meta=topic)

        await bot.reply_to(message, f"🎤 _Вы сказали:_ «{recognized_text}»", parse_mode="Markdown")
        await bot.send_chat_action(chat_id, "typing")

        reply = await asyncio.wait_for(ask_ai(chat_id, recognized_text), timeout=18.0)
        await send_smart_reply(message, reply)

    except asyncio.TimeoutError:
        await bot.reply_to(
            message,
            "⏳ Извините, ответ формируется дольше обычного. Повторите, пожалуйста, запрос через пару секунд или напишите текстом."
        )
    except Exception as e:
        print(f"❌ Ошибка обработки голосового: {e}")
        await bot.reply_to(message, "⚠️ Не получилось обработать голосовое сообщение. Попробуйте написать текстом.")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@bot.message_handler(
    func=lambda msg: msg.text not in MENU_BUTTONS,
    content_types=["text"]
)
async def handle_text(message):
    if not await check_mode_selected(message):
        return

    chat_id = message.chat.id
    if not _check_rate_limit(chat_id):
        await bot.reply_to(message, "⏳ Слишком много запросов. Подождите минуту.")
        return

    topic = detect_service_topic(message.text)
    if topic:
        await log_analytics_event(chat_id, "ai_price_question", meta=topic)

    await bot.send_chat_action(chat_id, "typing")
    reply = await ask_ai(chat_id, message.text)
    await send_smart_reply(message, reply)