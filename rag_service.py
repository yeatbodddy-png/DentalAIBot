import os
import chromadb
from chromadb.utils import embedding_functions
from dental_data import CLINIC_INFO, SERVICES_TEXT, DOCTORS

# Создаем постоянное локальное хранилище ChromaDB в папке проекта
DB_PATH = "./chroma_db"
chroma_client = chromadb.PersistentClient(path=DB_PATH)

# Используем стандартную компактную модель для эмбеддингов
embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

# Получаем или создаем коллекцию знаний
collection = chroma_client.get_or_create_collection(
    name="dental_knowledge",
    embedding_function=embedding_fn
)


def init_vector_db():
    """Индексирует прайс, контакты и информацию о врачах, если база пуста."""
    if collection.count() == 0:
        documents = [
            f"Название клиники: {CLINIC_INFO['name']}. Адрес: {CLINIC_INFO['address']}. Телефон: {CLINIC_INFO['phone']}.",
            f"График работы клиники: {CLINIC_INFO['work_hours']}.",
            SERVICES_TEXT
        ]

        # Индексируем каждого врача отдельно для точечного поиска
        for doc_id, doc_info in DOCTORS.items():
            doc_text = f"Врач: {doc_info['name']}, Специализация: {doc_info['spec']}, Свободные слоты: {', '.join(doc_info['slots'])}"
            documents.append(doc_text)

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
        results = collection.query(
            query_texts=[query],
            n_results=n_results
        )
        retrieved_docs = results.get('documents', [[]])[0]
        return "\n\n".join(retrieved_docs)
    except Exception as e:
        print(f"⚠️ Ошибка при векторном поиске в ChromaDB: {e}")
        return ""