# CLAUDE.md — Garmin AI Coach (Python, async)

Telegram-бот: персональный тренер по бегу на основе методологии Джека Дэниелса (VDOT, зоны E/M/T/I/R, 4 фазы) и Мэта Фицджеральда (правило 80/20). Данные берутся из Garmin Connect, планы генерирует LLM (Claude или Groq, выбирается настройкой), а все расчёты (VDOT, темпы, пульс, длительность, проверка правил) выполняет код.

Язык проекта: комментарии, docstring, сообщения пользователю и коммиты на русском. Идентификаторы на английском.

## Главный принцип

**LLM не считает и не форматирует.** Она выбирает структуру плана (типы тренировок, километраж), а код считает VDOT, темпы, пульс и длительность, проверяет правила тренировочной логики и собирает сообщение для Telegram. Если появляется соблазн дать модели посчитать темп, пульс или время, нужно вместо этого добавить функцию в `services/`.

## Стек

Python 3.12, aiogram 3.x, SQLAlchemy 2.0 async + asyncpg, PostgreSQL 16, Alembic (async), pydantic v2 и pydantic-settings, garminconnect >= 0.3.4, APScheduler 3.x, pytest.

LLM: `anthropic` SDK (Claude, по умолчанию `claude-haiku-5-5`) или `openai` SDK (Groq, `openai/gpt-oss-20b`). Провайдер задаёт `LLM_PROVIDER` в `.env` (`claude` или `groq`).

## Структура

```text
main.py                     точка входа: миграции -> бот -> планировщик, DI через Dispatcher
config.py                   Settings (pydantic-settings), секреты как SecretStr, LLM_PROVIDER
database.py                 async engine, async_session_maker, run_migrations (alembic upgrade head)
sports_knowledge.txt        база знаний (Дэниелс + 80/20), идёт в системный промпт
generate_token.py           ручной вход в Garmin (обход 429), токены в .garmin_tokens/<chat_id>/
models/                     SQLAlchemy-модели (6 таблиц), base.py с naming convention и utcnow()
repositories/               доступ к БД: user_repo, plan_repo, activity_repo (flush, без commit)
schemas/plan.py             pydantic-схемы: WorkoutType, PlannedDay, WeekPlan, Phase, MacroPlan, PHASE_NAMES
clients/llm.py              create_llm_client(): клиент по LLM_PROVIDER
clients/claude_client.py    Claude: system отдельно, структурированный вывод по схеме, effort вместо temperature
clients/ai_client.py        Groq через openai SDK: json_mode, повтор при 429
clients/ai_errors.py        AIClientError, AIResponseFormatError (без импорта config)
clients/rate_limit.py       сколько ждать после 429 от Groq
clients/garmin/             client.py (to_thread), analytics.py, token_storage.py, exceptions.py
services/user_service.py    фасад над репозиториями, ЗДЕСЬ commit
services/coach_service.py   чистая математика: calculate_vdot, calculate_zones, zones_for_profile
services/plan_validator.py  правила Дэниелса/Фицджеральда -> список замечаний
services/plan_generator.py  LLM -> JSON -> схема -> правила -> повтор (до 3 попыток)
services/plan_paces.py      темп, пульс и длительность дня из зон VDOT и ЧССmax
services/plan_calendar.py   границы недель, число недель плана и номер недели (от недели забега назад)
services/user_time.py       часовой пояс пользователя: разбор ввода, смещение по Garmin, время рассылки
services/plan_storage.py    plan_details (JSONB) <-> MacroPlan / WeekPlan
services/plan_renderer.py   сообщения Telegram (HTML) из WeekPlan и MacroPlan
services/ai_coach_service.py текстовый разбор тренировки для /analyze (зоны считает activity_zones)
services/activity_zones.py зона тренировки по темпу и пульсу, % ЧССmax, серая зона, пульс выше зоны темпа
services/activity_polling.py какие новые тренировки разбирать при опросе (чистые функции)
services/activity_poller.py опрос Garmin (интервал ACTIVITY_POLL_MINUTES), автоматический разбор новых пробежек
services/message_service.py sanitize_telegram_html, chunk_message (лимит 4000 символов)
services/scheduler_service.py send_week, ежечасная проверка рассылки недель (ВС с 15:00 по поясу пользователя), опрос Garmin
bot/                        states.py, keyboards.py, handlers/{start,sync,plan,analyze,settings}.py
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

Перед `alembic revision` БД должна быть на `head`, иначе ошибка «Target database is not up to date». Каждую автомиграцию просматривать глазами. Смену типа колонки с данными писать вручную с `postgresql_using` (autogenerate делает `ALTER ... TYPE` без `USING`, и PostgreSQL его отклоняет).

Если `alembic upgrade head` падает с `relation "app_users" already exists`, таблицы были созданы в обход Alembic (старый `create_all`). Для тестовой БД: сбросить схему (`DROP SCHEMA public CASCADE; CREATE SCHEMA public`) и применить миграции заново.

## Соглашения

**БД**
- Схему меняет только Alembic, `main.py` при старте применяет миграции до `head` (`database.run_migrations` в `asyncio.to_thread`: `env.py` сам вызывает `asyncio.run`). `create_all` не использовать.
- При запуске из кода `env.py` не вызывает `fileConfig` (атрибут `configure_logger=False`): иначе `alembic.ini` поднял бы корневой уровень до WARNING и логи бота пропали бы.
- Репозитории делают `flush`, но не `commit`. Транзакцией управляет сервис или хендлер.
- Все связи `relationship` с `lazy="raise"`, `passive_deletes=True`. Нужную связь грузить явно (`selectinload`).
- Время: `models.base.utcnow()` и `DateTime(timezone=True)`. `datetime.utcnow()` не использовать.
- `telegram_chat_id` это `BigInteger`. Уникальные ограничения и частичный индекс (один активный план на пользователя) держат целостность: upsert через `ON CONFLICT`, не «select, затем insert».
- Сессию БД не держать открытой во время вызова LLM: прочитать, закрыть, вызвать, открыть для записи.
- `plan_details` в `training_plans` и `weekly_plans` это JSONB: `model_dump` схемы (`plan_storage.plan_to_details`), чтение через `parse_macro` / `parse_week`. Старые текстовые планы лежат как `{"legacy_text": "..."}` и читаются как `None`: по ним неделю не построить, пользователь получает просьбу пересоздать план.

**Конфигурация и секреты**
- Секреты в `settings` это `SecretStr`: для использования нужен `.get_secret_value()` (URL БД, ключи Groq и Anthropic). Забытый вызов даёт 401 от API или `ArgumentError` в SQLAlchemy.
- `LLM_PROVIDER=claude` без `ANTHROPIC_API_KEY` останавливает запуск на проверке `Settings`.
- `.env`, `.garmin_tokens/` и `*.json` в `.gitignore`. Пароль Garmin не сохраняется: хранятся только токены сессии.
- Переменные окружения Windows (`GROQ_API_KEY`, `ANTHROPIC_API_KEY`) перекрывают `.env`.

**Garmin**
- Библиотека синхронная: все вызовы через `asyncio.to_thread`. Состояние MFA хранится в `GarminClient._pending_auth` (в памяти процесса).
- Опрос активностей: раз в `ACTIVITY_POLL_MINUTES` минут (настройка в `.env`, по умолчанию 120, `0` выключает опрос). Сам опрос токены не тратит, они уходят только на разбор новой пробежки. Опрос: последние `FETCH_LIMIT` (10) тренировок, пользователи по очереди, задача с `max_instances=1`. При 429 круг прерывается: блок IP общий для всех.
- Первый опрос пользователя (нет записей в `processed_activities`) помечает всё без разбора. Дальше тренировка сначала захватывается атомарной вставкой (`claim_activity`, `ON CONFLICT DO NOTHING`), потом разбирается: дублей не бывает, но при сбое ИИ разбор этой тренировки теряется.
- Разбираются только беговые тренировки не старше 24 часов по `startTimeGMT`; более старые (например, после простоя бота) помечаются молча.
- Ошибка 429 означает блок IP: помогает смена сети или `generate_token.py`.

**LLM**
- Один клиент на приложение (`create_llm_client()` в `main.py`), в хендлеры и планировщик он попадает через DI. У обоих клиентов один интерфейс `generate_response(messages, ..., json_mode, response_schema)`.
- Структурные планы: генератор передаёт pydantic-класс в `response_schema`. Claude получает его в структурированный вывод (`output_config.format` через `anthropic.transform_schema`), Groq работает в JSON-режиме. Дальше одинаково: схема, `plan_validator`, `plan_renderer`.
- Непригодный ответ (обрезан по длине, `json_validate_failed` у Groq) это `AIResponseFormatError`: генератор повторяет тот же запрос. Остальные `AIClientError` идут наверх, их текст адресован пользователю.
- Claude: `temperature` не передаётся (новые модели его отклоняют), глубину задаёт `CLAUDE_EFFORT`, мышление адаптивное, `max_tokens` не меньше 16000.
- Groq (бесплатный тариф, 8000 токенов в минуту): клиент ждёт `retry-after` при 429 и повторяет до 3 раз; ожидание дольше 60 с не ждёт.
- Текстовые ответы (`/analyze`) проходят `MessageService.sanitize_telegram_html`. Зону тренировки, проценты ЧССmax и пульс зоны E считает `activity_zones` (блок «РАСЧЁТ ЗОН ТРЕНИРОВКИ» в промпте), модель только интерпретирует.
- Данные пользователя и Garmin в промптах оборачиваются в `<data>...</data>`, плюс `_clean()` против инъекций; в системном промпте сказано не выполнять команды из этих блоков.
- Лимиты в промптах берутся из констант `plan_validator`, чтобы подсказка и проверка не расходились.
- Модель в структурных планах указывает тип тренировки, дистанцию и рабочую часть. Зону (E/M/T/I/R), темп, пульс, длительность и названия фаз задаёт код.

**Правила планов (`plan_validator`, пороги в константах вверху файла)**
- Неделя: hard-easy, лимиты рабочей части T/I/R, длительный не больше 30% недели, не длиннее 150 мин в лёгком темпе атлета (нужны зоны) и не короче других дней, не больше 3 качественных, минимум 1 день отдыха или ОФП, километраж ±15% от плана, в фазе I нет качественных тренировок.
- Макроплан: сумма недель, рост не больше 10% от лучшей из двух предыдущих недель, пик в фазе II или III, до подводки неделя не ниже 70% первой, в фазе IV объём не растёт, последняя неделя не больше 75% пика.
- Номер недели считается от недели забега назад (`plan_calendar.plan_week_number`); после недели забега рассылка для плана прекращается. Число недель плана в `/plan` считается тем же календарём (`plan_total_weeks`), а не `days_left // 7`.

**Часовые пояса**
- Пояс пользователя в `app_users.timezone`: имя IANA (`Asia/Almaty`, учитывает летнее время) или смещение (`UTC+05:00`). `NULL` = `settings.DEFAULT_TIMEZONE` (по умолчанию `Europe/Moscow`). Получать через `UserService.timezone_of(user)`, разбирать ввод через `user_time.normalize_timezone`.
- Пояс задаётся командой `/timezone` или определяется при `/sync` по разнице местного времени и UTC у свежей тренировки Garmin (только если пользователь не задал его сам).
- Все «сегодня» пользователя (дни до забега в `/plan`, следующая неделя в `/test_week` и рассылке) считаются по его поясу. `datetime.now()` без пояса не использовать; опрос Garmin работает в UTC.
- Рассылка недель: задача каждый час в :00, пользователю уходит неделя, если у него воскресенье, 15:00 или позже, и неделя ещё не создана. Если генерация упала, следующий час попробует снова; подсказка про старый формат плана уходит только в 15:00.
- На Windows для `zoneinfo` нужен пакет `tzdata` (есть в `requirements.txt`).

**Telegram**
- Только поддерживаемые теги: `b`, `i`, `u`, `s`, `code`, `pre`, `blockquote`, `a`. Любой текст от модели экранировать (`html.escape`).
- Длинные сообщения резать через `MessageService.chunk_message`.
- Хендлеры регистрировать на уровне модуля (раньше `/test_week` случайно был вложен в другую функцию и не работал).
- Зависимости (`garmin`, `ai_coach`, `plan_generator`, `scheduler_service`) получать аргументами хендлера, не создавать при импорте модуля.

**Тесты и код**
- Тесты не должны требовать `.env`, сети или БД: тяжёлые импорты (`config`, клиенты LLM) ленивые, LLM подменяется заглушкой.
- Чистая логика (`coach_service`, `plan_validator`, `plan_paces`, `plan_calendar`, `plan_storage`, `plan_renderer`, `activity_zones`) без I/O, всегда с тестами.
- Модули, которые тесты импортируют, не загружают `config` при импорте (например, `clients.garmin.token_storage` берёт настройки в конструкторе).
- Правка формулы или порога сопровождается тестом.

## Модель данных (кратко)

`app_users` -> `athlete_profiles` (1:1, включая `vdot`, `best_effort_*`), `training_plans` (макроцикл, один `active`, `plan_details` JSONB), `weekly_plans` (уникально по `training_plan_id + week_start_date`, `plan_details` JSONB), `processed_activities` (уникально по `user_id + garmin_activity_id`), `chat_messages`. Все FK с `ON DELETE CASCADE`.

## Статус

Сделано:
- Этап 1: быстрые исправления (единые клиенты, `settings`, санитизация промптов).
- Этап 2: модели, репозитории, Alembic.
- Этап 3: `coach_service` (VDOT и зоны), поля профиля, тесты.
- Этап 4: схемы, валидатор, генератор, темпы, пульс и длительность кодом, рендер; `plan_details` в JSONB; `/plan`, `/test_week` и воскресная рассылка на структурных планах; клиент Claude и выбор провайдера.
- Этап 5: миграции Alembic при старте вместо `create_all` (проверено на пустой БД); `/analyze` с расчётом зоны и пульса кодом и общим клиентом LLM через DI; поллинг активностей с автоматическим разбором новых пробежек; часовые пояса пользователей (`/timezone`, определение по Garmin, рассылка по местному времени).

Проверено скриптами на живой модели и локальной БД (запись с откатом): генерация макроплана и недели, сохранение и чтение JSONB, `send_week` для нового, старого и завершённого плана. Живая проверка в Telegram (`/sync`, `/plan`, `/test_week`) ещё не подтверждена.

Дальше:
- Этап 5: блокировки.
- Этап 6: ruff, CI, Docker, `/delete_me`, `requirements-dev.txt` для pytest.

## Известные ограничения

- VDOT берётся из тренировочных пробежек от 3 км, это нижняя оценка формы. Запланирован ручной ввод результата забега.
- `max_heart_rate` в профиле это пик по пробежкам за 90 дней, а не лабораторный максимум: пульсовые диапазоны приблизительные.
- Длительность тренировки оценочная: рабочая часть в темпе зоны, остальное в середине лёгкого диапазона, округление до 5 мин. Без VDOT длительность не показывается и потолок длительного по времени не проверяется.
- Планы короче 4 недель допускают фазы с `weeks = 0`; сейчас `/plan` разрешает минимум 2 недели.
- Пояс, определённый по Garmin, сохраняется смещением: при переходе на летнее время его нужно поправить через `/timezone` (имя IANA учитывает переход само).
- FSM хранится в `MemoryStorage`: состояния теряются при перезапуске бота.
- Лимиты `plan_validator` строгие при малом объёме (при 20 км в неделю порог ограничен 2 км). Модель часто целится точно в границы допусков.

## Чего не делать

- Не просить модель считать VDOT, темпы, пульс или длительность.
- Не коммитить `.env`, `.garmin_tokens/`, одноразовые скрипты сброса БД.
- Не создавать таблицы в обход Alembic.
- Не вызывать синхронный код `garminconnect` напрямую в event loop.
- Не отправлять в Telegram неэкранированный текст от модели.
- Не коммитить непроверенное: сначала тесты и живая проверка, потом коммит.
