# Garmin AI Coach (Python Pure Async Edition) — Контекст проекта

## 1. Концепция и цель рефакторинга
**Garmin AI Coach** — это интеллектуальный персональный тренер по бегу в Telegram, построенный на принципах спортивной физиологии Джека Дэниелса («Формула бега», VDOT) и Мэта Фицджеральда («Бег по правилу 80/20»).

### Архитектурный переход (Java -> Pure Python):
* **Было:** Разделенная микросервисная архитектура (Java 21 Spring Boot + Python FastAPI Sidecar + PostgreSQL).
  * *Минусы:* Высокое потребление RAM (~800 МБ), задержки межсервисного HTTP-взаимодействия, дублирование сущностей, сложный multi-stage деплой.
* **Стало:** Единый асинхронный модульный монолит на **Python 3.12**.
  * *Плюсы:* Нативная работа с библиотекой `garminconnect` внутри одного процесса, потребление RAM ~100 МБ, единый Event Loop (AsyncIO), отсутствие сетевых задержек между бэкендом и сервисом Garmin.

---

## 2. Актуальный технологический стек

| Компонент | Технология | Назначение / Обоснование |
| :--- | :--- | :--- |
| **Язык платформы** | Python 3.12 | Современный синтаксис, строгая типизация, высокая производительность. |
| **Telegram Bot** | `aiogram 3.x` | Полностью асинхронный фреймворк с поддержкой роутеров, фильтров, FSM и WebApp Data. |
| **ORM / База данных** | `SQLAlchemy 2.0 (Async)` + `asyncpg` | Современный декларативный синтаксис `Mapped[]`, поддержка пула соединений, отсутствие блокировок потока. |
| **СУБД** | PostgreSQL 16 | Реляционная база данных для хранения профилей, планов и истории сообщений. |
| **Конфигурация** | `pydantic-settings 2.x` | Валидация переменных окружения при старте приложения (Fail-Fast принцип). |
| **Garmin API** | `garminconnect >= 0.3.4` | Работа с профилем, выгрузка тренировок, DI-токены сессий. |
| **LLM Инференс** | `openai >= 1.14.0` (Groq Cloud) | Модель `openai/gpt-oss-20b` через Groq API (`https://api.groq.com/openai/v1`). |
| **Планировщик** | `APScheduler 3.x` (`AsyncIOScheduler`) | Фоновые периодические задачи (Cron). |
| **Контроль версий** | Git + GitHub | Ветвление, защита секретов через `.gitignore`. |

---

## 3. Текущая файловая структура проекта

```text
garmin-ai-coach-py/
├── .env                          # Боевые секреты (в .gitignore, не коммитится)
├── .env.example                  # Публичный шаблон переменных окружения
├── .gitignore                    # Защита секретов, токенов, кэша и venv
├── requirements.txt              # Зафиксированные версии библиотек
├── sports_knowledge.txt          # База знаний по Дэниелсу и 80/20
├── config.py                     # Валидация настроек через Pydantic Settings
├── database.py                   # Async Engine, SessionMaker, get_db_session, init_models
│
├── models/                       # Модульный слой моделей (SQLAlchemy 2.0)
│   ├── __init__.py               # Re-export всех моделей и единая регистрация в Base
│   ├── base.py                   # Базовый класс Base (DeclarativeBase)
│   ├── user.py                   # Модель AppUser (Aggregate Root)
│   ├── athlete_profile.py        # Модель AthleteProfile (Паспорт за 90 дней)
│   ├── training_plan.py          # Модель TrainingPlan (Целевые макроциклы)
│   ├── weekly_plan.py            # Модель WeeklyPlan (Микроциклы Пн-Вс)
│   ├── activity.py               # Модель ProcessedActivity (Дедупликация)
│   └── message.py                # Модель ChatMessage (История диалога)
│
├── clients/                      # Внешние клиенты (в разработке)
│   ├── garmin_client.py          # Интеграция с Garmin Connect
│   └── ai_client.py              # Клиент Groq LLM
│
├── services/                     # Бизнес-логика (в разработке)
│   ├── coach_service.py          # Расчет зон темпа (VDOT) и тренировочных фаз
│   ├── message_service.py        # Санитизация HTML и нарезка длинных сообщений
│   └── scheduler_service.py      # Задачи по расписанию (APScheduler)
│
└── bot/                          # Интерфейс Telegram (в разработке)
    ├── states.py                 # FSM-состояния диалога
    ├── keyboards.py              # Клавиатуры (Inline и WebApp)
    └── handlers/                 # Обработчики команд и сообщений
```

---

## 4. Схема базы данных (Спроектированные таблицы)

```text
app_users
  ├── id (BIGSERIAL, PK)
  ├── telegram_chat_id (BIGINT, UNIQUE, INDEX)
  ├── username (VARCHAR(255), NULL)
  ├── first_name (VARCHAR(255), NULL)
  └── garmin_linked (BOOLEAN, DEFAULT FALSE)

athlete_profiles
  ├── id (BIGSERIAL, PK)
  ├── user_id (BIGINT, FK -> app_users.id ON DELETE CASCADE, UNIQUE)
  ├── raw_summary_text (TEXT, NULL)
  ├── average_weekly_km (FLOAT, NULL)
  ├── max_heart_rate (INTEGER, NULL)
  ├── typical_easy_heart_rate (INTEGER, NULL)
  ├── garmin_vo2_max (FLOAT, NULL)
  └── updated_at (TIMESTAMP)

training_plans (Макроциклы подготовки к забегу)
  ├── id (BIGSERIAL, PK)
  ├── user_id (BIGINT, FK -> app_users.id ON DELETE CASCADE)
  ├── target_race (VARCHAR(255))      -- e.g. "21.1 км (Полумарафон)"
  ├── race_date (DATE)
  ├── total_weeks (INTEGER)
  ├── plan_details (TEXT)             -- Периодизация по 4 фазам Дэниелса
  ├── active (BOOLEAN, DEFAULT TRUE)
  └── created_at (TIMESTAMP)

weekly_plans (Скорректированные микроциклы)
  ├── id (BIGSERIAL, PK)
  ├── user_id (BIGINT, FK -> app_users.id ON DELETE CASCADE)
  ├── training_plan_id (BIGINT, FK -> training_plans.id ON DELETE CASCADE)
  ├── week_start_date (DATE)
  ├── week_end_date (DATE)
  ├── plan_details (TEXT)             -- Расписание на неделю (Пн-Вс)
  └── created_at (TIMESTAMP)

processed_activities (Дедупликация тренировок)
  ├── id (BIGSERIAL, PK)
  ├── user_id (BIGINT, FK -> app_users.id ON DELETE CASCADE)
  ├── garmin_activity_id (VARCHAR(100), INDEX)
  └── processed_at (TIMESTAMP)

chat_messages (Память ассистента)
  ├── id (BIGSERIAL, PK)
  ├── user_id (BIGINT, FK -> app_users.id ON DELETE CASCADE)
  ├── role (VARCHAR(50))              -- "user" / "assistant"
  ├── content (TEXT)
  └── created_at (TIMESTAMP)
```

---

## 5. Важнейшие архитектурные решения и решенные проблемы

### 1. Защита от переполнения ID Telegram
* В Telegram идентификаторы чатов превышают допустимый диапазон 32-битного Integer (> 2.1 млрд). 
* В `AppUser.telegram_chat_id` строго используется тип `BigInteger`.

### 2. Предотвращение циклических импортов (Circular Imports)
* При разбиении моделей на отдельные файлы использован паттерн `if TYPE_CHECKING:` для импорта зависимых классов.
* В связях `relationship` имена моделей передаются строками (например, `relationship("TrainingPlan", ...)`), что откладывает связывание типов до момента полной инициализации метаданных.
* Пакет `models/__init__.py` служит единой точкой ре-экспорта и гарантирует, что `from models import Base` автоматически регистрирует все 6 таблиц в `Base.metadata`.

### 3. Асинхронная загрузка связанных сущностей (MissingGreenlet prevention)
* Во всех связях `relationship` явно выставлен параметр `lazy="selectin"`.
* Это исключает неявные синхронные I/O-запросы (Lazy Loading) и подгружает связанные сущности через быстрый SQL-запрос `WHERE user_id IN (...)`.
* В `async_sessionmaker` установлен параметр `expire_on_commit=False`, предотвращающий инвалидацию полей сущностей после вызова `session.commit()`.

### 4. Безопасность и Git-гигиена
* Настроена строгая фильтрация в `.gitignore`: исключены `.env`, кэш `.garmin_tokens`, виртуальные окружения `venv/` и скомпилированный байт-код `__pycache__`.
* Добавлен файл `.env.example` для безопасного развертывания проекта в новых средах.

---

## 6. Дорожная карта реализации (Roadmap)

- [x] **Урок 1:** Архитектура, стек зависимостей, виртуальное окружение, `config.py` (Pydantic Settings).
- [x] **Урок 2:** Асинхронный слой PostgreSQL (`database.py`), модульная структура `models/` (6 таблиц, связи, индексы).
- [ ] **Урок 3:** Модуль интеграции с Garmin Connect (`clients/garmin_client.py`): авторизация по DI-токенам, сохранение сессий в `.garmin_tokens/`, методы забора последней активности и 90-дневного профиля в неблокирующем режиме (`asyncio.to_thread`).
- [ ] **Урок 4:** Модуль спортивного ИИ (`clients/ai_client.py` и `services/ai_coach_service.py`): интеграция с Groq LLM, загрузка `sports_knowledge.txt`, генерация макро/микропланов, санитизация Telegram HTML.
- [ ] **Урок 5:** Telegram-бот на `aiogram 3`: диспетчеризация команд (`/start`, `/plan`, `/sync`, `/analyze`), FSM-диалоги выбора дистанции и даты, прием данных из WebApp.
- [ ] **Урок 6:** Фоновый планировщик (`APScheduler`): воскресная генерация планов (15:00) и фоновый поллинг тренировок (каждые 30 мин).
- [ ] **Урок 7:** Контейнеризация (`Dockerfile`, `docker-compose.yml`) и инструкция по развертыванию.q