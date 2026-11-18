import os
import tempfile
import base64
import docx
from config import bot, client, MODES, user_histories, user_modes, ADMIN_IDS
from ai_service import ask_ai, ask_ai_with_vision
from utils import get_keyboard, send_long_message, send_smart_reply, make_py_file
from dental_data import CLINIC_INFO, SERVICES_TEXT, DOCTORS
from database import add_booking, remove_booking_by_id, is_slot_booked, get_all_bookings, get_user_bookings
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# Временное хранилище шагов выбора врача/времени в памяти
appointments_storage = {}


def notify_all_admins(text, parse_mode="HTML"):
    """
    Надежная рассылка уведомлений всем администраторам из ADMIN_IDS с try-except.
    Если один из админов заблокировал бота, цикл не падает и идет дальше.
    """
    if not ADMIN_IDS:
        print("⚠️ Предупреждение: список ADMIN_IDS пуст, некому отправлять уведомления!")
        return

    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, parse_mode=parse_mode)
        except Exception as e:
            print(f"⚠️ Не удалось отправить уведомление администратору {admin_id}: {e}")


def check_mode_selected(message):
    """Проверяет, выбрал ли пользователь режим. Если нет — просит выбрать."""
    chat_id = message.chat.id
    if chat_id not in user_modes or user_modes[chat_id] is None:
        bot.reply_to(
            message,
            "🛑 Пожалуйста, сначала выберите роль для общения с помощью кнопок снизу 👇",
            reply_markup=get_keyboard()
        )
        return False
    return True


@bot.message_handler(commands=["start"])
def handle_start(message):
    chat_id = message.chat.id
    user_modes[chat_id] = None  # Сбрасываем при старте

    bot.send_chat_action(chat_id, "typing")

    welcome_text = (
        "👋 Здравствуйте! Добро пожаловать в нашу стоматологическую клинику в г. Алматы.\n\n"
        "Я ваш цифровой помощник. Сначала выберите, с кем вы хотите пообщаться:\n"
        "• <b>👩‍⚕️ Администратор</b> — ответит на вопросы по ценам, расчетам услуг и записи.\n"
        "• <b>🦷 Врач-консультант</b> — проконсультирует по медицинским вопросам и стоимости лечения.\n\n"
        "Выберите роль с помощью кнопок на клавиатуре снизу 👇"
    )
    bot.send_message(chat_id, welcome_text, parse_mode="HTML", reply_markup=get_keyboard())


@bot.message_handler(commands=["reset"])
def handle_reset(message):
    chat_id = message.chat.id
    current_mode = user_modes.get(chat_id, "👩‍⚕️ Администратор")

    if chat_id in user_histories and current_mode in user_histories[chat_id]:
        user_histories[chat_id][current_mode] = []

    bot.reply_to(
        message,
        f"🧹 Память режима *{current_mode}* очищена! Начинаем диалог с чистого листа.",
        parse_mode="Markdown"
    )


# 👑 КОМАНДА АДМИНИСТРАТОРА (ПОСТАВЛЕНА ВЫШЕ ТЕКСТОВЫХ ХЕНДЛЕРОВ, ЧТОБЫ ЕЁ НЕ ПЕРЕХВАТЫВАЛ ИИ!)
@bot.message_handler(commands=["admin"])
def handle_admin_panel(message):
    chat_id = message.chat.id

    if chat_id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ У вас нет доступа к панели администратора.")
        return

    active_bookings = get_all_bookings()

    if not active_bookings:
        bot.reply_to(message, "📋 <b>Панель управления ресепшена:</b>\n\nВ данный момент активных записей нет.",
                     parse_mode="HTML")
        return

    markup = InlineKeyboardMarkup(row_width=1)
    text = "📋 <b>Панель ресепшена: Активные записи пациентов</b>\n\nНажмите на запись, чтобы отменить её и освободить слот:\n"

    for booking_id, booking in active_bookings.items():
        doctor = booking["doctor"]
        time_slot = booking["time"]
        patient_info = booking.get("patient", "Пациент")
        phone = booking.get("phone", "Не указан")

        text += f"\n🆔 [Запись #{booking_id}] 👤 <b>{patient_info}</b>\n📱 Тел: <b>{phone}</b>\n👨‍⚕️ {doctor} 🕒 <b>{time_slot}</b>\n"

        btn_text = f"❌ Отменить (#{booking_id}): {patient_info[:8]}... ({time_slot})"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"adm_cancel_{booking_id}"))

    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


# Обработка переключения режимов и кликов по главным кнопкам меню
@bot.message_handler(func=lambda msg: msg.text in ["👩‍⚕️ Администратор", "🦷 Врач-консультант"])
def handle_mode_switch(message):
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

    bot.reply_to(
        message,
        f"✅ Режим успешно установлен: <b>{message.text}</b>.\n\nТеперь вы можете выбрать интересующий вас раздел из меню ниже или задать любой вопрос в чате! 👇",
        parse_mode="HTML",
        reply_markup=menu_markup
    )


# Обработка кнопки «📋 Мои записи»
@bot.message_handler(func=lambda msg: msg.text == "📋 Мои записи")
def handle_my_appointments(message):
    if not check_mode_selected(message):
        return

    chat_id = message.chat.id
    user_bookings = get_user_bookings(chat_id)

    if not user_bookings:
        bot.reply_to(
            message,
            "📭 У вас пока нет активных записей на прием.\n\nВы можете записаться через кнопку «📅 Записаться» в меню.",
            parse_mode="HTML"
        )
        return

    text = "📋 <b>Ваши активные записи в клинику:</b>\n\n"
    for idx, (booking_id, doctor, time_slot, phone) in enumerate(user_bookings, 1):
        text += f"{idx}. 👨‍⚕️ Врач: <b>{doctor}</b>\n    🕒 Время: <b>{time_slot}</b>\n    📱 Телефон: <b>{phone}</b>\n\n"

    text += "📍 Адрес: г. Алматы, ул. Абая 150 (угол ул. Сатпаева)\nЖдем вас!"

    bot.reply_to(message, text, parse_mode="HTML")


@bot.message_handler(func=lambda msg: msg.text == "🔄 Сменить роль")
def handle_change_role(message):
    chat_id = message.chat.id
    user_modes[chat_id] = None
    bot.reply_to(
        message,
        "Выберите желаемую роль:",
        reply_markup=get_keyboard()
    )


# Обработка кнопки «Услуги»
@bot.message_handler(func=lambda msg: msg.text == "🩺 Услуги")
def handle_services(message):
    if not check_mode_selected(message):
        return
    chat_id = message.chat.id
    bot.send_chat_action(chat_id, "typing")

    photo_path = "photos/price_menu.webp"
    if os.path.exists(photo_path):
        try:
            with open(photo_path, "rb") as photo_file:
                bot.send_photo(
                    chat_id,
                    photo_file,
                    caption="📋 <b>Наши услуги и актуальный прайс-лист (₸):</b>",
                    parse_mode="HTML"
                )
        except Exception as e:
            print(f"⚠️ Не удалось отправить фото: {e}")

    send_long_message(message, SERVICES_TEXT)


# Обработка кнопки «📅 Записаться»
@bot.message_handler(func=lambda msg: msg.text == "📅 Записаться")
def handle_book_start(message):
    if not check_mode_selected(message):
        return

    chat_id = message.chat.id

    markup = InlineKeyboardMarkup(row_width=1)
    for doc_id, doc_info in DOCTORS.items():
        btn_text = f"{doc_info['name']} ({doc_info['spec']})"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"doc_{doc_id}"))

    bot.send_message(
        chat_id,
        "🗓 <b>Онлайн-запись в клинику (г. Алматы)</b>\n\nВыберите врача, к которому хотите записаться:",
        parse_mode="HTML",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("doc_"))
def callback_choose_doctor(call):
    chat_id = call.message.chat.id
    doc_id = call.data.replace("doc_", "")

    if doc_id not in DOCTORS:
        bot.answer_callback_query(call.id, "❌ Врач не найден.")
        return

    doc = DOCTORS[doc_id]

    if chat_id not in appointments_storage:
        appointments_storage[chat_id] = {}
    appointments_storage[chat_id]["doc_id"] = doc_id
    appointments_storage[chat_id]["doctor"] = doc["name"]

    markup = InlineKeyboardMarkup(row_width=2)

    available_slots_found = False
    for time_slot in doc["slots"]:
        if is_slot_booked(doc["name"], time_slot):
            continue

        available_slots_found = True
        markup.add(InlineKeyboardButton(f"🕒 {time_slot}", callback_data=f"time_{doc_id}_{time_slot}"))

    markup.add(InlineKeyboardButton("⬅️ Назад к списку врачей", callback_data="back_to_docs"))

    text = f"Вы выбрали: <b>{doc['name']}</b>\nСпециализация: {doc['spec']}\n\n"
    if available_slots_found:
        text += "Выберите свободное время для приема:"
    else:
        text += "⚠️ У этого врача пока нет свободных мест на ближайшее время."

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("time_"))
def callback_choose_time(call):
    chat_id = call.message.chat.id
    parts = call.data.split("_")
    time_slot = parts[-1]

    doc_name = appointments_storage.get(chat_id, {}).get("doctor", "Врач")

    if is_slot_booked(doc_name, time_slot):
        bot.answer_callback_query(call.id, "⚠️ Упс! Это время только что заняли. Выберите другое.", show_alert=True)
        return

    appointments_storage[chat_id]["time"] = time_slot

    contact_markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    contact_btn = KeyboardButton("📱 Поделиться номером телефона", request_contact=True)
    contact_markup.add(contact_btn)

    bot.send_message(
        chat_id,
        f"✅ Вы выбрали: <b>{doc_name}</b> на время <b>{time_slot}</b>.\n\n"
        "Последний шаг! Пожалуйста, поделитесь вашим номером телефона с помощью кнопки ниже, чтобы администратор клиники мог связаться с вами:",
        parse_mode="HTML",
        reply_markup=contact_markup
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(content_types=["contact", "text"])
def handle_phone_number(message):
    chat_id = message.chat.id

    if chat_id not in appointments_storage or "doctor" not in appointments_storage[chat_id] or "time" not in appointments_storage[chat_id]:
        return

    doc_name = appointments_storage[chat_id]["doctor"]
    time_slot = appointments_storage[chat_id]["time"]

    if message.contact:
        phone = message.contact.phone_number
    elif message.text and any(char.isdigit() for char in message.text):
        phone = message.text.strip()
    else:
        bot.reply_to(message, "⚠️ Пожалуйста, отправьте номер телефона с помощью кнопки ниже или напишите его цифрами.")
        return

    user = message.from_user
    user_fullname = user.first_name or "Без имени"
    user_display = f"{user_fullname} (@{user.username})" if user.username else f"{user_fullname} (ID: {user.id})"

    if is_slot_booked(doc_name, time_slot):
        bot.reply_to(message, "⚠️ К сожалению, это время только что занял другой пациент. Нажмите «📅 Записаться» в меню заново.")
        if chat_id in appointments_storage:
            del appointments_storage[chat_id]
        return

    add_booking(chat_id, doc_name, time_slot, user_display, phone)
    del appointments_storage[chat_id]

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
        f"🕒 Время: <b>{time_slot}</b>"
    )
    notify_all_admins(admin_notification_text)

    bot.send_message(
        chat_id,
        "✅ <b>Запись успешно оформлена! Ждем вас в клинике.</b>\n\n"
        f"📍 Адрес: г. Алматы, ул. Абая 150.\n"
        f"👨‍⚕️ Врач: <b>{doc_name}</b>\n"
        f"🕒 Время: <b>{time_slot}</b>\n"
        f"📱 Ваш телефон: <b>{phone}</b>\n\n"
        "Администратор свяжется с вами для подтверждения визита.",
        parse_mode="HTML",
        reply_markup=menu_markup
    )


@bot.callback_query_handler(func=lambda call: call.data == "back_to_docs")
def callback_back_to_docs(call):
    chat_id = call.message.chat.id
    markup = InlineKeyboardMarkup(row_width=1)
    for doc_id, doc_info in DOCTORS.items():
        btn_text = f"{doc_info['name']} ({doc_info['spec']})"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"doc_{doc_id}"))

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="🗓 Выберите врача, к которому хотите записаться:",
        parse_mode="HTML",
        reply_markup=markup
    )


@bot.message_handler(func=lambda msg: msg.text == "📍 Адрес")
def handle_address(message):
    if not check_mode_selected(message):
        return
    bot.reply_to(message, f"{CLINIC_INFO['name']}\n\n{CLINIC_INFO['address']}\n{CLINIC_INFO['phone']}")


@bot.message_handler(func=lambda msg: msg.text == "🕒 График работы")
def handle_work_hours(message):
    if not check_mode_selected(message):
        return
    bot.reply_to(message, CLINIC_INFO['work_hours'])


# Обработка отмены записи со стороны администратора (с уведомлением пациента)
@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_cancel_"))
def callback_admin_cancel(call):
    admin_chat_id = call.message.chat.id

    if admin_chat_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ Недостаточно прав.", show_alert=True)
        return

    booking_id = int(call.data.replace("adm_cancel_", ""))

    # 🔍 Сначала получаем информацию о записи (включая user_id пациента) перед удалением
    active_bookings = get_all_bookings()
    booking_info = active_bookings.get(booking_id)

    # Удаляем запись по её ID и освобождаем слот в расписании
    removed_data = remove_booking_by_id(booking_id)

    if not removed_data or not booking_info:
        bot.answer_callback_query(call.id, "⚠️ Эта запись уже была удалена или не существует.", show_alert=True)
        return

    doctor = removed_data["doctor"]
    time_slot = removed_data["time"]
    patient_chat_id = booking_info["user_id"]  # Достаем Telegram ID пациента

    bot.answer_callback_query(call.id, "✅ Запись отменена, слот снова свободен!")

    # 📩 ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ ПАЦИЕНТУ ОБ ОТМЕНЕ ВИЗИТА
    try:
        patient_notification = (
            "❌ <b>К сожалению, ваша запись была отменена администратором клиники.</b>\n\n"
            f"👨‍⚕️ Врач: <b>{doctor}</b>\n"
            f"🕒 Время: <b>{time_slot}</b>\n\n"
            "Если у вас возникли вопросы вы можете позвонить Нам,либо если вы хотите перенести запись на другое время, вы можете выбрать нового врача через кнопку «📅 Записаться» в меню."
        )
        bot.send_message(patient_chat_id, patient_notification, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Не удалось отправить уведомление пациенту {patient_chat_id}: {e}")

    # Обновляем сообщение в чате администратора
    bot.edit_message_text(
        chat_id=admin_chat_id,
        message_id=call.message.message_id,
        text=f"✅ Запись #{booking_id} к врачу <b>{doctor}</b> на <b>{time_slot}</b> успешно отменена. Пациенту отправлено уведомление, слот освобожден.",
        parse_mode="HTML"
    )

# 🤖 ОБЫЧНЫЕ ТЕКСТОВЫЕ ВОПРОСЫ К ИИ (ПОСТАВЛЕНЫ В САМЫЙ НИЗ)
@bot.message_handler(func=lambda msg: True, content_types=["text"])
def handle_text(message):
    if not check_mode_selected(message):
        return

    chat_id = message.chat.id
    bot.send_chat_action(chat_id, "typing")

    reply = ask_ai(chat_id, message.text)
    send_smart_reply(message, reply)


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    if not check_mode_selected(message):
        return

    chat_id = message.chat.id
    bot.send_chat_action(chat_id, "typing")
    temp_path = None

    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
            temp_audio.write(downloaded_file)
            temp_path = temp_audio.name

        with open(temp_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(temp_path), audio_file.read()),
                model="whisper-large-v3-turbo"
            )

        recognized_text = transcription.text.strip()
        if not recognized_text:
            bot.reply_to(message, "Не удалось разобрать слова в голосовом сообщении 🤷‍♂️ Попробуйте еще раз или напишите текстом.")
            return

        bot.reply_to(message, f"🎤 _Вы сказали:_ «{recognized_text}»", parse_mode="Markdown")
        bot.send_chat_action(chat_id, "typing")

        reply = ask_ai(chat_id, recognized_text)
        send_smart_reply(message, reply)

    except Exception as e:
        print(f"❌ Ошибка обработки голосового: {e}")
        bot.reply_to(message, "⚠️ Не получилось обработать голосовое сообщение. Попробуйте написать текстом.")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)