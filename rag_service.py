import chromadb
from chromadb.utils import embedding_functions
from dental_data import CLINIC_INFO, SERVICES_TEXT

# Создаем постоянное локальное хранилище ChromaDB в папке проекта
DB_PATH = "chroma_db"
_chroma_client = None
_embedding_fn = None
_collection = None


def _get_collection():
    """Лениво инициализирует клиент ChromaDB, модель эмбеддингов и коллекцию."""
    global _chroma_client, _embedding_fn, _collection

    if _collection is None:
        if _chroma_client is None:
            _chroma_client = chromadb.PersistentClient(path=DB_PATH)

        if _embedding_fn is None:
            _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )

        _collection = _chroma_client.get_or_create_collection(
            name="dental_knowledge",
            embedding_function=_embedding_fn
        )

    return _collection


def init_vector_db():
    """Индексирует информацию о клинике и прайс-лист, если база пуста."""
    collection = _get_collection()

    if collection.count() == 0:
        documents = [
            f"Название клиники: {CLINIC_INFO['name']}. Адрес: {CLINIC_INFO['address']}. Телефон: {CLINIC_INFO['phone']}.",
            f"График работы клиники: {CLINIC_INFO['work_hours']}.",
            SERVICES_TEXT
        ]

        ids = [f"knowledge_item_{i}" for i in range(len(documents))]

        collection.add(
            documents=documents,
            ids=ids
        )
        print("✅ Векторная база знаний ChromaDB успешно создана и проиндексирована!")
    else:
        print("📦 Векторная база знаний ChromaDB уже загружена.")


def search_knowledge(query, n_results=2):
    """Ищет наиболее релевантные фрагменты информации по запросу пользователя."""
    try:
        collection = _get_collection()
        results = collection.query(
            query_texts=[query],
            n_results=n_results
        )
        retrieved_docs = results.get('documents', [[]])[0]
        return "\n\n".join(retrieved_docs)
    except Exception as e:
        print(f"⚠️ Ошибка при векторном поиске в ChromaDB: {e}")
        return ""