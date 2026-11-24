import asyncio
import time
from config import client, MODES, user_histories, user_modes
from rag_service import init_vector_db, search_knowledge
from utils import now_almaty

_vector_db_initialized = False
_warmup_done = False
_warmup_lock = asyncio.Lock()


def ensure_vector_db():
    """Ленивая инициализация векторной базы ChromaDB без блокировки потока при импорте."""
    global _vector_db_initialized
    if not _vector_db_initialized:
        init_vector_db()
        _vector_db_initialized = True


def get_working_models():
    """Синхронно и надежно собирает актуальные модели Groq при старте с приоритетом быстрых моделей."""
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
            if "llama-3.3-70b" in low:
                return 0
            if "llama-3.1-8b" in low or "instant" in low:
                return 1
            if "llama" in low and any(tag in low for tag in ["70b", "90b", "405b", "maverick", "scout"]):
                return 2
            if "llama" in low:
                return 3
            if "gpt-oss-20b" in low:
                return 4
            if "gpt-oss" in low:
                return 5
            if "qwen" in low or "deepseek" in low:
                return 6
            return 7

        valid.sort(key=priority)
        if valid:
            print(f"📋 Порядок моделей Groq: {valid[:5]}")
        return valid
    except Exception as e:
        print(f"⚠️ Не удалось получить список моделей: {e}")
        return []


MODELS = get_working_models()


async def initialize_ai_models():
    """Асинхронная инициализация моделей при старте."""
    global MODELS
    if not MODELS:
        MODELS = get_working_models()


async def warmup_ai_services():
    """
    Прогревает ChromaDB и эмбеддинги в фоне при старте бота, чтобы векторный поиск
    был готов к работе без обрыва по раннему таймауту.
    """
    global _warmup_done
    async with _warmup_lock:
        if _warmup_done:
            return

        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        try:
            print("🔥 Прогреваем векторную базу ChromaDB и поисковый индекс...")
            await asyncio.wait_for(loop.run_in_executor(None, ensure_vector_db), timeout=180)
            await asyncio.wait_for(
                loop.run_in_executor(None, lambda: search_knowledge("стоматология прайс запись", n_results=2)),
                timeout=60
            )
            _warmup_done = True
            print(f"✅ Прогрев ChromaDB и ИИ завершён за {time.perf_counter() - t0:.2f}s!")
        except Exception as e:
            print(f"⚠️ Прогрев прошёл с предупреждением (бот продолжит работу): {e!r}")


async def ask_ai(chat_id, user_text):
    """Асинхронный RAG-запрос в Groq с контекстом ChromaDB и мгновенным переключением моделей."""
    if not MODELS:
        await initialize_ai_models()
    if not MODELS:
        return "Не удалось загрузить список моделей Groq. Проверь интернет или API_KEY!"

    loop = asyncio.get_running_loop()

    current_mode = user_modes.get(chat_id, "👩‍⚕️ Администратор")
    system_prompt = MODES.get(current_mode, list(MODES.values())[0])
    almaty_now_str = now_almaty().strftime("%Y-%m-%d %H:%M")

    relevant_context = ""
    try:
        relevant_context = await asyncio.wait_for(
            loop.run_in_executor(None, lambda q=user_text: search_knowledge(q, n_results=2)),
            timeout=1.5
        )
    except Exception as e:
        print(f"⚠️ Поиск по базе знаний пропущен/замедлился: {e}")

    rag_block = (
        f"\n\n📚 АКТУАЛЬНЫЕ ДАННЫЕ ИЗ БАЗЫ ЗНАНИЙ КЛИНИКИ ПО ЭТОМУ ВОПРОСУ:\n{relevant_context}"
        if relevant_context else ""
    )

    enhanced_system_prompt = (
        f"{system_prompt}{rag_block}\n\n"
        f"🕒 ТЕКУЩЕЕ ВРЕМЯ В АЛМАТЫ (GMT+5 / Asia/Almaty): {almaty_now_str}. "
        "Если пациент спрашивает о текущем времени или дате, опирайся строго на это время по Алматы.\n"
        "🛑 Напоминание: отвечай тепло, чётко и по делу (без лишней воды), всегда дописывай мысль до конца "
        "и не обрывай слова/фразы. Понимай русский, казахский и смешанную речь, отвечая на языке основного "
        "обращения пациента. Не записывай через чат (только кнопка «📅 Записаться»), не выводи простыни "
        "со списком врачей и слотов, не назначай скидки/подарки сам — направляй на ресепшн по номеру +7 (777) 123-45-67."
    )

    if chat_id not in user_histories:
        user_histories[chat_id] = {}
    if current_mode not in user_histories[chat_id]:
        user_histories[chat_id][current_mode] = []

    history = user_histories[chat_id][current_mode]
    recent_history = history[-4:]

    messages_payload = (
        [{"role": "system", "content": enhanced_system_prompt}]
        + recent_history
        + [{"role": "user", "content": user_text}]
    )

    last_error = None

    for idx, model_name in enumerate(MODELS):
        model_timeout = 8.0 if idx == 0 else 6.0
        try:
            print(f"🚀 Пробуем отправить запрос в модель Groq: {model_name}")
            completion = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda m=model_name: client.chat.completions.create(
                        model=m,
                        messages=messages_payload,
                        temperature=0.6,
                        max_tokens=900
                    )
                ),
                timeout=model_timeout
            )
            reply_text = completion.choices[0].message.content
            print(f"✅ Успешный ответ от модели {model_name}")

            updated_history = history + [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": reply_text}
            ]
            user_histories[chat_id][current_mode] = updated_history[-12:]

            return reply_text

        except asyncio.TimeoutError:
            print(f"⏱️ Модель {model_name} отвечала дольше {model_timeout} сек, пробуем следующую...")
            last_error = TimeoutError(f"Timeout on {model_name}")
            continue
        except Exception as e:
            print(f"⚠️ Модель {model_name} сбоит, пробуем следующую... Ошибка: {e}")
            last_error = e
            continue

    print(f"❌ Критическая ошибка: все модели Groq недоступны. Последняя ошибка: {last_error}")
    return "Извините, система консультаций сейчас немного перегружена. Пожалуйста, повторите запрос через пару секунд!"


async def ask_ai_with_vision(chat_id, user_prompt, base64_image):
    """Асинхронная функция для отправки текста и фото/рентгена/прайса в мультимодальную модель Groq"""
    try:
        current_mode = user_modes.get(chat_id, "👩‍⚕️ Администратор")
        system_prompt = MODES.get(current_mode, list(MODES.values())[0])

        loop = asyncio.get_running_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model="qwen/qwen2.5-vl-7b-instruct",
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
                    max_tokens=800
                )
            ),
            timeout=20
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ Ошибка при анализе изображения: {e}"