from config import client, MODES, user_histories, user_modes

def get_working_models():
    """Автоматически собирает актуальные текстовые модели Groq и отсеивает лишние."""
    excluded = [
        "whisper", "guard", "tts", "audio", "vision",
        "embed", "rerank", "allam", "playai", "orpheus"
    ]
    try:
        all_models = client.models.list().data
        valid = [
            m.id for m in all_models
            if not any(bad in m.id.lower() for bad in excluded)
        ]

        def priority(name):
            low = name.lower()
            if "llama" in low and any(tag in low for tag in ["70b", "90b", "405b", "maverick", "scout"]):
                return 0
            if "qwen" in low or "deepseek" in low:
                return 1
            if "llama" in low:
                return 2
            return 3

        valid.sort(key=priority)
        return valid
    except Exception as e:
        print(f"⚠️ Не удалось получить список моделей: {e}")
        return []

MODELS = get_working_models()

def ask_ai(chat_id, user_text):
    """Отправляет текстовый запрос в Groq с учетом истории конкретного режима клиники."""
    if not MODELS:
        return "Не удалось загрузить список моделей Groq. Проверь интернет или API_KEY!"

    # 🔄 Изменили дефолтный режим на Администратора
    current_mode = user_modes.get(chat_id, "👩‍⚕️ Администратор")
    system_prompt = MODES.get(current_mode, list(MODES.values())[0])

    if chat_id not in user_histories:
        user_histories[chat_id] = {}
    if current_mode not in user_histories[chat_id]:
        user_histories[chat_id][current_mode] = []

    history = user_histories[chat_id][current_mode]

    # 🔥 ЗАЩИТА ОТ ЛИМИТОВ: Берем только последние 8 сообщений из истории
    recent_history = history[-8:]

    messages_payload = (
        [{"role": "system", "content": system_prompt}]
        + recent_history
        + [{"role": "user", "content": user_text}]
    )

    last_error = None
    for model_name in MODELS:
        try:
            completion = client.chat.completions.create(
                model=model_name,
                messages=messages_payload,
                temperature=0.7,
                max_tokens=1000  # Ограничение длины ответа
            )
            reply_text = completion.choices[0].message.content

            # Обновление истории чата (храним до 20 последних)
            updated_history = history + [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": reply_text}
            ]
            user_histories[chat_id][current_mode] = updated_history[-20:]

            return reply_text

        except Exception as e:
            print(f"⚠️ Модель {model_name} сбоит, пробуем следующую... Ошибка: {e}")
            last_error = e
            continue

    print(f"❌ Критическая ошибка: все модели Groq недоступны. Последняя ошибка: {last_error}")
    return "Извините, система консультаций сейчас немного перегружена. Пожалуйста, повторите запрос через пару секунд!"

def ask_ai_with_vision(chat_id, user_prompt, base64_image):
    """Функция для отправки текста и фото/рентгена/прайса в мультимодальную модель Groq"""
    try:
        current_mode = user_modes.get(chat_id, "👩‍⚕️ Администратор")
        system_prompt = MODES.get(current_mode, list(MODES.values())[0])

        response = client.chat.completions.create(
            model="qwen/qwen2.5-vl-7b-instruct",  # Или актуальная vision-модель Groq
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            temperature=0.7,
            max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        return (f"❌ Ошибка при анализе изображения: {e}")