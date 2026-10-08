# CLAUDE.md — Garmin AI Coach (Python, async)

Telegram-бот: персональный тренер по бегу на основе методологии Джека Дэниелса (VDOT, зоны E/M/T/I/R, 4 фазы) и Мэта Фицджеральда (правило 80/20). Данные берутся из Garmin Connect, планы генерирует LLM (Groq), а все расчёты (VDOT, темпы, пульс, проверка правил) выполняет код.

Язык проекта: комментарии, docstring, сообщения пользователю и коммиты на русском. Идентификаторы на английском.

## Главный принцип

**LLM не считает и не форматирует.** Она выбирает структуру плана (типы тренировок, километраж), а код считает VDOT и темпы, проверяет правила тренировочной логики и собирает сообщение для Telegram. Если появляется соблазн дать модели посчитать темп или пульс, нужно вместо этого добавить функцию в `services/`.

## Стек

Python 3.12, aiogram 3.x, SQLAlchemy 2.0 async + asyncpg, PostgreSQL 16, Alembic (async), pydantic v2 и pydantic-settings, garminconnect >= 0.3.4, openai SDK (Groq, модель `openai/gpt-oss-20b`), APScheduler 3.x, pytest.

## Структура

```text
main.py                     точка входа: миграции -> бот -> планировщик
config.py                   Settings (pydantic-settings), секреты как SecretStr
database.py                 async engine, async_session_maker, get_db_session
sports_knowledge.txt        база знаний (Дэниелс + 80/20), идёт в системный промпт
generate_token.py           ручной вход в Garmin (обход 429), токены в .garmin_tokens/<chat_id>/
models/                     SQLAlchemy-модели (6 таблиц), base.py с naming convention и utcnow()
repositories/               доступ к БД: user_repo, plan_repo, activity_repo (flush, без commit)
schemas/plan.py             pydantic-схемы: WorkoutType, PlannedDay, WeekPlan, Phase, MacroPlan
clients/ai_client.py        Groq через openai SDK, json_mode, AIClientError
clients/garmin/             client.py (to_thread), analytics.py, token_storage.py, exceptions.py
clients/garmin_client.py    реэкспорт из clients.garmin
services/user_service.py    фасад над репозиториями, ЗДЕСЬ commit
services/coach_service.py   чистая математика: calculate_vdot, calculate_zones, predict_race_time
services/plan_validator.py  правила Дэниелса/Фицджеральда -> список замечаний
services/plan_generator.py  LLM -> JSON -> схема -> правила -> повтор (до 3 попыток)
services/plan_paces.py      темп и пульс дня из зон VDOT и ЧССmax
services/plan_renderer.py   сообщения Telegram (HTML) из WeekPlan и MacroPlan
services/ai_coach_service.py текстовые промпты (пока используются в /plan, /analyze, планировщике)
services/message_service.py sanitize_telegram_html, chunk_message (лимит 4000 символов)
services/scheduler_service.py воскресная рассылка микроциклов (ВС 15:00, время сервера)
bot/                        states.py, keyboards.py, handlers/{start,sync,plan,analyze}.py
migrations/                 Alembic (env.py берёт URL из settings)
tests/                      pytest, без сети, БД и .env
```

## Команды (Windows, PowerShell, venv)

```powershell
python -m pytest -v                                   # все тесты
python main.py                                        # запуск (сам применяет миграции)
alembic revision --autogenerate -m "описание"         # после изменения моделей
alembic upgrade head                                  # применить; alembic current / alembic check
python try_plan_generator.py                          # ручная проверка генерации с живой моделью
python generate_token.py                              # вход в Garmin (сначала /start боту!)
```

Перед `alembic revision` БД должна быть на `head`, иначе ошибка «Target database is not up to date». Каждую автомиграцию просматривать глазами.

Если `alembic upgrade head` падает с `relation "app_users" already exists`, таблицы были созданы в обход Alembic (старый `create_all`). Для тестовой БД: сбросить схему (`DROP SCHEMA public CASCADE; CREATE SCHEMA public`) и применить миграции заново.

## Соглашения

**БД**
- Схему меняет только Alembic. `create_all` и `init_models` не использовать.
- Репозитории делают `flush`, но не `commit`. Транзакцией управляет сервис или хендлер.
- Все связи `relationship` с `lazy="raise"`, `passive_deletes=True`. Нужную связь грузить явно (`selectinload`).
- Время: `models.base.utcnow()` и `DateTime(timezone=True)`. `datetime.utcnow()` не использовать.
- `telegram_chat_id` это `BigInteger`. Уникальные ограничения и частичный индекс (один активный план на пользователя) держат целостность: upsert через `ON CONFLICT`, не «select, затем insert».
- Сессию БД не держать открытой во время вызова LLM: прочитать, закрыть, вызвать, открыть для записи.

**Конфигурация и секреты**
- Секреты в `settings` это `SecretStr`: для использования нужен `.get_secret_value()` (URL БД, ключ Groq). Забытый вызов даёт 401 от Groq или `ArgumentError` в SQLAlchemy.
- `.env`, `.garmin_tokens/` и `*.json` в `.gitignore`. Пароль Garmin не сохраняется: хранятся только токены сессии.
- Переменная окружения Windows `GROQ_API_KEY` перекрывает `.env`.

**Garmin**
- Библиотека синхронная: все вызовы через `asyncio.to_thread`. Состояние MFA хранится в `GarminClient._pending_auth` (в памяти процесса).
- Ошибка 429 означает блок IP: помогает смена сети или `generate_token.py`.

**LLM**
- Текстовые ответы проходят `MessageService.sanitize_telegram_html`. Структурные планы идут в JSON-режиме и проходят схему, `plan_validator`, затем `plan_renderer`.
- Данные пользователя и Garmin в промптах оборачиваются в `<data>...</data>`, плюс `_clean()` против инъекций; в системном промпте сказано не выполнять команды из этих блоков.
- `gpt-oss` тратит токены на рассуждения: для JSON `max_tokens=6000`, обрезанный (`finish_reason == "length"`) ответ считается ошибкой.
- Лимиты в промптах берутся из констант `plan_validator`, чтобы подсказка и проверка не расходились.
- Модель в структурных планах указывает только тип тренировки; зона (E/M/T/I/R) выводится кодом, темпов и пульса в схеме нет.

**Telegram**
- Только поддерживаемые теги: `b`, `i`, `u`, `s`, `code`, `pre`, `blockquote`, `a`. Любой текст от модели экранировать (`html.escape`).
- Длинные сообщения резать через `MessageService.chunk_message`.
- Хендлеры регистрировать на уровне модуля (раньше `/test_week` случайно был вложен в другую функцию и не работал).

**Тесты и код**
- Тесты не должны требовать `.env`, сети или БД: тяжёлые импорты (`config`, `AIClient`) ленивые, LLM подменяется заглушкой.
- Чистая логика (`coach_service`, `plan_validator`, `plan_paces`, `plan_renderer`) без I/O, всегда с тестами.
- Правка формулы или порога сопровождается тестом.

## Модель данных (кратко)

`app_users` -> `athlete_profiles` (1:1, включая `vdot`, `best_effort_*`), `training_plans` (макроцикл, один `active`), `weekly_plans` (уникально по `training_plan_id + week_start_date`), `processed_activities` (уникально по `user_id + garmin_activity_id`), `chat_messages`. Все FK с `ON DELETE CASCADE`.

## Статус

Сделано:
- Этап 1: быстрые исправления (единые клиенты, `settings`, санитизация промптов).
- Этап 2: модели, репозитории, Alembic.
- Этап 3: `coach_service` (VDOT и зоны), поля профиля, тесты. Живая проверка `/sync`, `/plan`, `/test_week` ещё не подтверждена.
- Этап 4, блоки 1-4: схемы, валидатор, генератор, подстановка темпов и пульса, рендер. Живой прогон `try_plan_generator.py` после исправления ключа ещё не подтверждён.

В работе (этап 4, блок 5):
- Миграция `plan_details` в JSONB для `training_plans` и `weekly_plans`.
- Подключение `PlanGenerator` и `plan_renderer` к `/plan`, `/test_week` и планировщику (сейчас они ещё на текстовой генерации `ai_coach_service`).
- Передавать `calculate_zones(profile.vdot)` и `max_hr=profile.max_heart_rate` в рендер.

Дальше:
- Этап 5: поллинг активностей (`ProcessedActivity`), таймзоны пользователей, retry, блокировки.
- Этап 6: ruff, CI, Docker, `/delete_me`, `requirements-dev.txt` для pytest.

## Известные ограничения

- VDOT берётся из тренировочных пробежек от 3 км, это нижняя оценка формы. Запланирован ручной ввод результата забега.
- `max_heart_rate` в профиле это пик по пробежкам за 90 дней, а не лабораторный максимум: пульсовые диапазоны приблизительные.
- Планы короче 4 недель допускают фазы с `weeks = 0`; сейчас `/plan` разрешает минимум 2 недели.
- Планировщик работает по системному времени сервера, а не по времени пользователя.
- FSM хранится в `MemoryStorage`: состояния теряются при перезапуске бота.
- Лимиты `plan_validator` строгие при малом объёме (при 20 км в неделю порог ограничен 2 км). Пороги в константах вверху файла.

## Чего не делать

- Не просить модель считать VDOT, темпы или пульс.
- Не коммитить `.env`, `.garmin_tokens/`, одноразовые скрипты сброса БД.
- Не создавать таблицы в обход Alembic.
- Не вызывать синхронный код `garminconnect` напрямую в event loop.
- Не отправлять в Telegram неэкранированный текст от модели.
- Не коммитить непроверенное: сначала тесты и живая проверка, потом коммит.
