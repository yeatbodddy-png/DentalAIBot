import os
import re
import time
import threading
import chromadb
from chromadb.utils import embedding_functions
from dental_data import CLINIC_INFO, SERVICES_TEXT

# Избегаем лишних сетевых проверок HuggingFace при наличии локального кэша модели
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

DB_PATH = "chroma_db"
_chroma_client = None
_embedding_fn = None
_collection = None
_chroma_lock = threading.RLock()

_FALLBACK_DOCS: list[str] = []

_WORD_RE = re.compile(r"[а-яёәіңғүұқөһa-z0-9]+")

_BILINGUAL_SYNONYMS = {
    "кариес": ["кариес", "пломба", "пломбу", "лечение", "зуб", "зуба", "зубов", "тіс", "тісті", "емдеу", "пломбалау"],
    "канал": ["канал", "каналы", "каналов", "пульпит", "нерв", "корневых", "түбір", "арна", "жүйке"],
    "удаление": ["удаление", "удалить", "вырвать", "мудрости", "восьмерка", "восьмёрка", "алу", "жұлдыру", "ақыл", "тісін"],
    "имплант": ["имплант", "имплантация", "импланты", "протез", "мост", "импланттау", "протездеу"],
    "коронка": ["коронка", "коронки", "коронку", "металлокерамика", "цирконий", "циркониевая", "протезирование", "қаптама", "цирконийлі"],
    "винир": ["винир", "виниры", "виниров", "керамический", "эстетика", "винір", "керамикалық"],
    "брекеты": ["брекеты", "брекетов", "брекет", "челюсть", "прикус", "ортодонтия", "металлические", "эстетические", "жақ", "тістеу"],
    "цена": ["цена", "цены", "стоит", "стоимость", "прайс", "сколько", "тенге", "₸", "смета", "баға", "бағасы", "қанша", "тұрады"],
    "контакты": ["адрес", "где", "находитесь", "алматы", "абая", "сатпаева", "телефон", "позвонить", "ресепшн", "мекенжай", "қайда", "нөмір", "хабарласу"],
    "график": ["график", "работаете", "часы", "время", "открываетесь", "выходной", "воскресенье", "суббота", "жұмыс", "уақыты", "кестесі", "демалыс"],
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _expand_query(query: str) -> str:
    words = set(_WORD_RE.findall((query or "").lower()))
    expanded = set(words)
    for group in _BILINGUAL_SYNONYMS.values():
        if words & set(group):
            expanded.update(group)
    return " ".join(sorted(expanded)) if expanded else (query or "")


def _build_fallback_docs() -> list[str]:
    global _FALLBACK_DOCS
    if not _FALLBACK_DOCS:
        clinic_contacts = _strip_html(
            f"Название клиники / Клиника атауы: {CLINIC_INFO['name']}. "
            f"{CLINIC_INFO['address']}. "
            f"{CLINIC_INFO['phone']}."
        )
        clinic_hours = _strip_html(
            f"Клиника: {CLINIC_INFO['name']}. {CLINIC_INFO['work_hours']}."
        )
        services_clean = _strip_html(SERVICES_TEXT)
        _FALLBACK_DOCS = [clinic_contacts, clinic_hours, services_clean]
    return _FALLBACK_DOCS


def _fallback_search(query: str, n_results: int = 2) -> str:
    docs = _build_fallback_docs()
    words = set(_WORD_RE.findall(_expand_query(query).lower()))
    if not words:
        return "\n\n".join(docs[:n_results])

    scored = []
    for idx, doc in enumerate(docs):
        doc_low = doc.lower()
        score = sum(1 for w in words if w in doc_low)
        scored.append((score, -idx, doc))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    top = [doc for score, _, doc in scored[:n_results] if score > 0]
    return "\n\n".join(top or docs[:n_results])


def _get_collection():
    """Потокобезопасно лениво инициализирует клиент ChromaDB, модель эмбеддингов и коллекцию."""
    global _chroma_client, _embedding_fn, _collection

    with _chroma_lock:
        if _collection is None:
            t0 = time.perf_counter()
            if _chroma_client is None:
                _chroma_client = chromadb.PersistentClient(path=DB_PATH)

            if _embedding_fn is None:
                _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2"
                )

            _collection = _chroma_client.get_or_create_collection(
                name="dental_knowledge",
                embedding_function=_embedding_fn,
            )
            print(f"🧠 Индекс ChromaDB и эмбеддинги готовы за {time.perf_counter() - t0:.2f}s")

    return _collection


def init_vector_db():
    """Индексирует информацию о клинике и прайс-лист в ChromaDB, если база пуста."""
    _build_fallback_docs()
    with _chroma_lock:
        collection = _get_collection()

        if collection.count() == 0:
            documents = [
                _strip_html(
                    f"Название клиники: {CLINIC_INFO['name']}. "
                    f"{CLINIC_INFO['address']}. "
                    f"{CLINIC_INFO['phone']}."
                ),
                _strip_html(
                    f"Клиника: {CLINIC_INFO['name']}. {CLINIC_INFO['work_hours']}."
                ),
                _strip_html(SERVICES_TEXT),
            ]
            ids = [f"knowledge_item_{i}" for i in range(len(documents))]

            collection.add(
                documents=documents,
                ids=ids,
            )
            print("✅ Векторная база знаний ChromaDB успешно создана и проиндексирована!")
        else:
            print("📦 Векторная база знаний ChromaDB уже загружена.")


def search_knowledge(query, n_results=2):
    """
    Ищет релевантные фрагменты в ChromaDB с расширением RU/KZ синонимов.
    Если индекс ещё не прогрузился или возникла ошибка, мгновенно возвращает данные из фолбэка.
    """
    expanded_query = _expand_query(query)

    if _collection is None:
        return _fallback_search(expanded_query, n_results=n_results)

    try:
        with _chroma_lock:
            results = _collection.query(
                query_texts=[expanded_query],
                n_results=n_results,
            )
        retrieved_docs = results.get("documents", [[]])[0]
        if retrieved_docs:
            return "\n\n".join(retrieved_docs)
    except Exception as e:
        print(f"⚠️ Ошибка при векторном поиске в ChromaDB, используем фолбэк: {e}")

    return _fallback_search(expanded_query, n_results=n_results)