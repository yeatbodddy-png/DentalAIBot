# 🦷 Dental Clinic AI Assistant Bot (`DentalBot`)

A full-featured commercial Telegram bot for a dental clinic in Almaty, powered by Artificial Intelligence (Groq API), vector knowledge base retrieval (RAG via ChromaDB), a 1C/CRM integration layer, end-to-end `async/await` concurrency, automated 24-hour patient appointment reminders, multimodality (text, images, voice messages), a smart pricing calculator in Kazakhstani Tenge (₸), interactive paginated online booking, and a protected reception admin dashboard.

> **Language:** English | [🇷🇺 Русская версия](README_RU.md)

---

## 🚀 Key Features

- **⚡ Full Asynchronous Architecture (`async/await`) & Instant First Response:** Message handlers, database operations, CRM synchronization, the background notification scheduler, and Telegram polling are built entirely on `asyncio` coroutines. Blocking calls to Groq and vector search are offloaded to background threads (`run_in_executor`), while startup warmup (`warmup_ai_services`) preloads embeddings and the search index—ensuring fast responses right from the user's very first message without cold-start delays.
- **🤝 Empathetic AI Acting Like a Real Clinic Receptionist:** Rather than giving generic or robotic replies, the bot communicates warmly, attentively, and to the point—just like an experienced reception specialist and dental consultant of the clinic. It understands procedures, reassures anxious patients, and strictly follows business guardrails: bookings are only processed via the interactive keyboard ("📅 Записаться"), raw schedule dumps are prevented in chat, and requests for discounts, promotions, or special terms are routed directly to reception with the clinic's phone number.
- **🧠 Context-Aware Dual Personas:** Seamless switching between **Administrator** (pricing consultations, cost estimates, booking guidance) and **Doctor-Consultant** (expert medical recommendations and treatment cost orientation).
- **🔍 Retrieval-Augmented Generation (RAG):** Pulls relevant clinic and service details from a local **ChromaDB** vector store with startup warmup, thread-safe initialization, and timeout protection.
- **🧮 Smart Cost Estimator:** Knows the clinic's exact price list in **Kazakhstani Tenge (₸)** and automatically calculates combined estimates for multi-procedure treatment plans.
- **📍 Localization & Almaty Timezone (`GMT+5` / `Asia/Almaty`):** Tailored to the clinic's physical location in **Almaty** (150 Abay Ave, corner of Satpayev St) and anchored to `Asia/Almaty` (`+05:00`) for visit timestamps, 24-hour background reminders, AI time context, and 1C/CRM requests—regardless of the host machine's system timezone.
- **📋 Interactive Service Catalog:** Sends a `.webp` price list graphic paired with a structured breakdown of service categories.
- **📅 Interactive Online Booking & 1C / CRM Integration:**
  - Fetches doctors with pagination (scalable to 50–100+ specialists), retrieves live time slots, and synchronizes booking creation/cancellation through `crm_service.py` (passing `Asia/Almaty` / `+05:00` timezone metadata, a reusable HTTP session, and automatic retries on transient `500`/`503` errors; bundled with a local FastAPI/Uvicorn simulator, `mock_1c_server.py`).
  - Inline selection of doctors by specialty and available appointment slots.
  - **One-Tap Contact Collection:** Collects patient phone numbers effortlessly via Telegram's `request_contact` button or numeric input.
  - **Double-Booking Protection:** Booked slots are automatically hidden from the schedule and locked atomically via `asyncio.Lock` and `PRAGMA busy_timeout` in SQLite, as well as on the 1C/CRM side (`409 Conflict`).
- **⏰ Automated 24-Hour Appointment Reminders:** A background worker (`reminder_24h_worker`) checks upcoming visits in `clinic.db` using Almaty time (`GMT+5`, storing `appointment_dt` and deduplication flag `reminded_24h`) and sends a Telegram reminder 24 hours prior to the visit, remaining resilient across bot restarts.
- **🔒 Reception Admin Dashboard:**
  - Hidden `/admin` command restricted to authorized clinic staff via `ADMIN_IDS`.
  - Lists active bookings with patient names, `@username` handles, and contact phone numbers.
  - One-click cancellation that **immediately releases the slot** in the schedule, syncs with 1C/CRM (`DELETE /api/bookings`), and **automatically notifies the patient**.
- **🎤 Voice Message Support (Whisper):** Transcribes audio messages using Groq's `whisper-large-v3-turbo` model with speech quality checks and a graceful fallback to text input when audio is unclear.
- **🧹 Memory Management:** Clears conversation context within the current persona via `/reset`.
- **🛡️ Resilient Delivery & Failover:** Expanded token budget to prevent mid-sentence cutoffs, safe delivery of long replies with plain-text fallback if Telegram rejects Markdown formatting, generation timeouts, and automatic failover across backup Groq models during delays or rate limits.

---

## 🧪 Reliability & Stress Testing

The system has been verified under realistic commercial stress scenarios with zero critical crashes, database corruption, or lost appointments:

- **Slot Race Condition Protection:** When multiple users attempt to book the exact same doctor slot simultaneously, only one booking succeeds in `clinic.db` while duplicates are rejected atomically and blocked by `409 Conflict` in 1C—with zero `database is locked` errors.
- **1C Latency & `500`/`503` Fault Tolerance:** Under simulated 6-second 1C delays and `500`/`503` server errors, the bot remains responsive, continues answering other patients in parallel, and retries transient CRM failures gracefully.
- **Extended Multi-Turn Sessions (18+ Messages):** Maintains conversational coherence across persona switches (`👩‍⚕️ Administrator` $\leftrightarrow$ `🦷 Doctor-Consultant`), accurately computes multi-item Tenge (₸) estimates, and strictly enforces clinic guardrails.
- **Concurrent Patient Load (10 Simultaneous Users):** Handles parallel patient inquiries reliably through automatic Groq model failover; for peak commercial traffic (20+ simultaneous requests per minute), capacity scales seamlessly by upgrading to Groq's paid `Developer tier` (higher TPM/RPM limits) with zero code changes required.

---

## 🛠 Tech Stack

- **Language:** Python 3.10+ (`asyncio`, `async/await`, `zoneinfo` with `Asia/Almaty`)
- **Databases & Vector Search:**
  - **SQLite (`aiosqlite`)** (`clinic.db`) — non-blocking storage for appointments, phone numbers, visit timestamps (`appointment_dt`), reminder states (`reminded_24h`), and booked slots with atomic double-booking protection, `PRAGMA busy_timeout`, `WAL` mode, and `AUTOINCREMENT`.
  - **ChromaDB** + `sentence-transformers` (`all-MiniLM-L6-v2`) — local vector knowledge base for RAG retrieval with thread-safe initialization.
- **Integration & APIs:**
  - `aiohttp` — asynchronous HTTP client with session pooling, `Asia/Almaty` (`+05:00`) timezone headers/parameters, and retry logic for two-way 1C/CRM synchronization.
  - `FastAPI` + `Uvicorn` (`mock_1c_server.py`) — local 1C simulator for testing doctor pagination, live availability slots, and `409` booking conflicts.
- **Libraries:**
  - `pyTelegramBotAPI` (`AsyncTeleBot`) — asynchronous Telegram bot framework, inline keyboards, and access control.
  - `Groq API` — LLMs (`openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/...`, `llama-...`) and `whisper-large-v3-turbo` for speech-to-text.
  - `python-dotenv` — secure environment variable management.

---

## 📂 Project Structure

```text
DentalBot/
│
├── main.py             # Entry point, AI service warmup, 24h reminder background worker (GMT+5), and polling loop
├── handlers.py         # Async handlers for commands, doctor pagination, booking, phone input, voice, text, and /admin
├── ai_service.py       # Groq API integration, warmup, model timeout/failover, RAG context injection, and vision analysis
├── rag_service.py      # Thread-safe ChromaDB initialization and vector knowledge search
├── crm_service.py      # 1C/CRM integration layer (paginated doctors, live slots, 500/503 retries, booking sync, GMT+5)
├── mock_1c_server.py   # Local 1C simulator (50 doctors, live slots, 409 conflict validation)
├── database.py         # Async SQLite manager via aiosqlite (appointments, timestamps, reminder queue, slot locks)
├── dental_data.py      # Clinic knowledge base (price list, working hours, contact details)
├── utils.py            # Almaty timezone helpers (GMT+5), safe async message delivery, and utilities
├── config.py           # Configuration, API keys, admin IDs, and persona system prompts with business guardrails
├── photos/             # Media assets (service price list in .webp format)
├── chroma_db/          # Local ChromaDB vector storage (auto-generated)
├── clinic.db           # SQLite database file (auto-generated)
├── .env.example        # Environment variables template
└── .gitignore          # Ignored files (protects tokens, databases, vector stores, and caches)