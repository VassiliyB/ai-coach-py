# Образ бота: только боевые зависимости, запуск под непривилегированным пользователем.
# Миграции бот применяет сам при старте (main.py -> database.run_migrations).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости отдельным слоем: при правке кода не переустанавливаются
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Токены Garmin пишутся в том (/app/.garmin_tokens): каталог создаётся заранее с правами пользователя бота,
# новый именованный том наследует их при первом подключении
RUN useradd --create-home --uid 1000 coach \
    && mkdir -p /app/.garmin_tokens \
    && chown -R coach:coach /app/.garmin_tokens
USER coach

# exec-форма: SIGTERM от docker stop получает сам Python, aiogram корректно останавливает опрос
CMD ["python", "main.py"]
