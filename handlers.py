import os
import tempfile
import base64
import docx
from config import bot, client, MODES, user_histories, user_modes, ADMIN_IDS
from ai_service import ask_ai, ask_ai_with_vision
from utils import get_keyboard, send_long_message, send_smart_reply, make_py_file
from dental_data import CLINIC_INFO, SERVICES_TEXT, DOCTORS, BOOKED_SLOTS, ACTIVE_BOOKINGS
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# Временное хранилище записей пациентов (в памяти бота)
appointments_storage = {}


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


# Обработка переключения режимов и кликов по главным кнопкам меню
@bot.message_handler(func=lambda msg: msg.text in ["👩‍⚕️ Администратор", "🦷 Врач-консультант"])
def handle_mode_switch(message):
    chat_id = message.chat.id
    user_modes[chat_id] = message.text

    # После выбора роли показываем главное меню клиники (включая кнопку «📅 Записаться»)
    menu_markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    menu_markup.add(
        KeyboardButton("🩺 Услуги"),
        KeyboardButton("📅 Записаться"),
        KeyboardButton("📍 Адрес"),
        KeyboardButton("🕒 График работы"),
        KeyboardButton("🔄 Сменить роль")
    )

    bot.reply_to(
        message,
        f"✅ Режим успешно установлен: <b>{message.text}</b>.\n\nТеперь вы можете выбрать интересующий вас раздел из меню ниже или задать любой вопрос (например, рассчитать комплексное лечение) в чате! 👇",
        parse_mode="HTML",
        reply_markup=menu_markup
    )


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

    # Путь к файлу в формате .webp
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

    # Следом отправляем подробный текстовый список услуг из dental_data.py
    send_long_message(message, SERVICES_TEXT)


# Обработка кнопки «📅 Записаться» (Инлайн-окошки с врачами)
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


# Обработка выбора врача (нажатие на инлайн-кнопку)
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
    appointments_storage[chat_id]["doc_id"] = doc_id  # Сохраняем ID врача для проверки слотов
    appointments_storage[chat_id]["doctor"] = doc["name"]

    markup = InlineKeyboardMarkup(row_width=2)

    # Динамически фильтруем слоты: показываем только те, которые НЕ заняты
    available_slots_found = False
    for time_slot in doc["slots"]:
        if (doc["name"], time_slot) in BOOKED_SLOTS:
            continue  # Пропускаем уже занятое время

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


# Обработка выбора времени приема с защитой от двойного бронирования (Double Booking)
@bot.callback_query_handler(func=lambda call: call.data.startswith("time_"))
def callback_choose_time(call):
    chat_id = call.message.chat.id
    user = call.from_user  # Получаем данные о пользователе, который жмет на кнопку

    parts = call.data.split("_")
    time_slot = parts[-1]  # Время (например, "10:00")

    doc_name = appointments_storage.get(chat_id, {}).get("doctor", "Врач")

    if (doc_name, time_slot) in BOOKED_SLOTS:
        bot.answer_callback_query(call.id, "⚠️ Упс! Это время только что заняли. Выберите другое.", show_alert=True)
        return

    # Сохраняем слот и ИМЯ/ЮЗЕРНЕЙМ пользователя для ресепшена
    BOOKED_SLOTS.add((doc_name, time_slot))

    # Формируем красивое имя для отображения
    user_fullname = user.first_name or "Без имени"
    if user.username:
        user_display = f"{user_fullname} (@{user.username})"
    else:
        user_display = f"{user_fullname} (ID: {user.id})"

    ACTIVE_BOOKINGS[chat_id] = {
        "doctor": doc_name,
        "time": time_slot,
        "patient": user_display  # Сохраняем информацию о пациенте
    }

    if chat_id in appointments_storage:
        appointments_storage[chat_id]["time"] = time_slot

    confirmation_text = (
        "✅ <b>Запись успешно оформлена!</b>\n\n"
        f"📍 Клиника: г. Алматы, ул. Абая 150 (угол ул. Сатпаева).\n"
        f"👨‍⚕️ Врач: <b>{doc_name}</b>\n"
        f"🕒 Время: <b>{time_slot}</b>\n\n"
        "Мы ждем вас! Если вы захотите отменить визит, пожалуйста, свяжитесь с нашим администратором по телефону."
    )

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=confirmation_text,
        parse_mode="HTML"
    )

    bot.answer_callback_query(call.id, "Запись успешно оформлена!")


# Кнопка возврата к списку врачей
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


# Обработка кнопки «Адрес»
@bot.message_handler(func=lambda msg: msg.text == "📍 Адрес")
def handle_address(message):
    if not check_mode_selected(message):
        return
    bot.reply_to(message, f"{CLINIC_INFO['name']}\n\n{CLINIC_INFO['address']}\n{CLINIC_INFO['phone']}")


# Обработка кнопки «График работы»
@bot.message_handler(func=lambda msg: msg.text == "🕒 График работы")
def handle_work_hours(message):
    if not check_mode_selected(message):
        return
    bot.reply_to(message, CLINIC_INFO['work_hours'])


# Команда для панели администратора (ресепшена)
@bot.message_handler(commands=["admin"])
def handle_admin_panel(message):
    chat_id = message.chat.id

    if chat_id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ У вас нет доступа к панели администратора.")
        return

    if not ACTIVE_BOOKINGS:
        bot.reply_to(message, "📋 <b>Панель управления ресепшена:</b>\n\nВ данный момент активных записей нет.",
                     parse_mode="HTML")
        return

    markup = InlineKeyboardMarkup(row_width=1)
    text = "📋 <b>Панель ресепшена: Активные записи пациентов</b>\n\nНажмите на запись, чтобы отменить её и освободить слот:\n"

    for user_id, booking in ACTIVE_BOOKINGS.items():
        doctor = booking["doctor"]
        time_slot = booking["time"]
        patient_info = booking.get("patient", "Пациент")  # Достаем имя/юзернейм

        # Теперь кнопка и текст содержат имя того, кто записался!
        text += f"\n👤 <b>{patient_info}</b>\n👨‍⚕️ {doctor} 🕒 <b>{time_slot}</b>\n"

        btn_text = f"❌ Отменить: {patient_info[:15]}... ({time_slot})"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"adm_cancel_{user_id}"))

    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


# Обработка отмены записи со стороны администратора (ресепшена)
@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_cancel_"))
def callback_admin_cancel(call):
    admin_chat_id = call.message.chat.id

    if admin_chat_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ Недостаточно прав.", show_alert=True)
        return

    target_user_id = int(call.data.replace("adm_cancel_", ""))

    if target_user_id not in ACTIVE_BOOKINGS:
        bot.answer_callback_query(call.id, "⚠️ Эта запись уже удалена или не существует.", show_alert=True)
        return

    booking = ACTIVE_BOOKINGS.pop(target_user_id)
    doctor = booking["doctor"]
    time_slot = booking["time"]

    # Возвращаем слот в расписание
    if (doctor, time_slot) in BOOKED_SLOTS:
        BOOKED_SLOTS.remove((doctor, time_slot))

    bot.answer_callback_query(call.id, "✅ Запись отменена, слот снова свободен!")

    bot.edit_message_text(
        chat_id=admin_chat_id,
        message_id=call.message.message_id,
        text=f"✅ Запись к врачу <b>{doctor}</b> на <b>{time_slot}</b> успешно отменена администратором. Слот освобожден в расписании.",
        parse_mode="HTML"
    )

    # Опционально уведомляем самого пациента об отмене администратором
    try:
        bot.send_message(
            target_user_id,
            f"ℹ️ Уважаемый пациент, ваша запись к врачу <b>{doctor}</b> на <b>{time_slot}</b> была отменена администратором клиники.",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Не удалось отправить уведомление пользователю: {e}")


# Обработка обычных текстовых вопросов к ИИ (калькулятор цен, консультации и т.д.)
@bot.message_handler(func=lambda msg: True, content_types=["text"])
def handle_text(message):
    if not check_mode_selected(message):
        return

    chat_id = message.chat.id
    bot.send_chat_action(chat_id, "typing")

    # Отправляем запрос ИИ с учетом ролей (Администратор или Врач знает цены в тенге и адрес в Алматы)
    reply = ask_ai(chat_id, message.text)
    send_smart_reply(message, reply)


# Обработка голосовых сообщений (Whisper + ИИ-администратор/врач)
@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    if not check_mode_selected(message):
        return

    chat_id = message.chat.id
    bot.send_chat_action(chat_id, "typing")
    temp_path = None

    try:
        # Скачиваем голосовой файл из Telegram
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        # Сохраняем во временный .ogg файл
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
            temp_audio.write(downloaded_file)
            temp_path = temp_audio.name

        # Отправляем в Whisper API для распознавания речи
        with open(temp_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(temp_path), audio_file.read()),
                model="whisper-large-v3-turbo"
            )

        recognized_text = transcription.text.strip()
        if not recognized_text:
            bot.reply_to(message,
                         "Не удалось разобрать слова в голосовом сообщении 🤷‍♂️ Попробуйте еще раз или напишите текстом.")
            return

        # Показываем пользователю, что именно бот услышал
        bot.reply_to(message, f"🎤 _Вы сказали:_ «{recognized_text}»", parse_mode="Markdown")
        bot.send_chat_action(chat_id, "typing")

        # Отправляем распознанный текст нашей ИИ-модели (Администратору или Врачу)
        reply = ask_ai(chat_id, recognized_text)
        send_smart_reply(message, reply)

    except Exception as e:
        print(f"❌ Ошибка обработки голосового: {e}")
        bot.reply_to(message, "⚠️ Не получилось обработать голосовое сообщение. Попробуйте написать текстом.")
    finally:
        # Очищаем за собой временный файл
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)