# Nailshop Bot

**Release:** v1.0.0  
**Status:** готов к production deploy после заполнения реального `.env` владельцем проекта.

Telegram-бот для одного nail-мастера: онлайн-запись, услуги и цены, портфолио работ, контакты и социальные сети, управление расписанием через админ-панель.

## Стек

- Python 3.10+; Docker image использует `python:3.10-slim`, релиз проверен на Python 3.11
- aiogram 3.x
- SQLite (локально/дев) / PostgreSQL 16 (прод)
- FileStorage (дев) / Redis (прод)
- APScheduler
- Docker / Docker Compose

## Быстрый запуск

### 1. Настройка переменных окружения

```bash
cp .env.example .env
# Отредактируйте .env и заполните обязательные поля:
BOT_TOKEN=ваш_токен_из_BotFather
ADMIN_IDS=ваш_telegram_id
```

### 2a. Запуск через Docker

```bash
docker-compose up -d
```

### 2b. Запуск напрямую

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

### 3. Первичная настройка

После запуска отправьте боту `/admin` и откройте:
- **Настройки** — задайте название студии, имя мастера, адрес, телефон и часы работы.
- **Услуги** — отредактируйте список услуг, цены и длительность.
- **Блокировки времени** — закройте полный день, отдельный слот или диапазон времени.
- **Портфолио** — добавьте фото работ.
- **Соц.сети** — добавьте ссылки на Instagram, TikTok, WhatsApp и т.п.

## Документация релиза

- `PRODUCTION_RUNBOOK.md` — пошаговый production runbook: deploy, backup, restore, rollback, recovery.
- `RELEASE_CHECKLIST.md` — чек-лист перед релизом и Git tag.
- `RELEASE_NOTES_v1.0.0.md` — краткие release notes для владельца и клиента.
- `CHANGELOG.md` — история изменений и состав релиза.

## Инструкции для владельца

### Установка

1. Распакуйте release ZIP или склонируйте репозиторий на сервер.
2. Скопируйте `.env.example` в `.env`.
3. Заполните `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL` и production-настройки.
4. Запустите `docker compose build`.
5. Запустите `docker compose up -d`.
6. Проверьте `docker compose exec bot python healthcheck.py`.

### Настройка бизнеса

После первого запуска используйте `/admin`: настройте услуги, цены, длительность, контакты, портфолио, соцсети и блокировки времени.

### Обновление

1. Сделайте backup: `docker compose exec bot python backup.py`.
2. Сохраните текущий `.env` и файл backup вне каталога релиза.
3. Распакуйте новый release ZIP или обновите Git checkout.
4. Выполните `docker compose build` и `docker compose up -d`.
5. Проверьте `healthcheck.py`, логи и smoke test в Telegram.

### Резервные копии и восстановление

- PostgreSQL backup создаётся через `pg_dump` в `backups/*.sql.gz`.
- SQLite backup создаётся через SQLite backup API в `backups/*.db.gz`.
- Для PostgreSQL restore drill используйте disposable database и `POSTGRES_RESTORE_TEST_DATABASE_URL`.
- Для полного восстановления на новом сервере перенесите `.env`, backup-файл и восстановите PostgreSQL dump перед запуском бота.

### Смена токена Telegram

1. Создайте новый token в @BotFather.
2. Остановите bot: `docker compose stop bot`.
3. Замените `BOT_TOKEN` в `.env`.
4. Запустите bot: `docker compose up -d bot`.
5. Проверьте healthcheck и отправьте `/start` новому боту.

### Перенос на другой сервер

1. На старом сервере создайте backup и остановите bot.
2. На новом сервере установите Docker, Docker Compose и распакуйте релиз.
3. Скопируйте `.env` и backup-файл.
4. Восстановите PostgreSQL dump в новую БД.
5. Запустите `docker compose up -d` и проверьте healthcheck, логи и Telegram smoke test.

## Главное меню клиента

| Кнопка | Описание |
|--------|----------|
| Записаться | Флоу записи: услуга → дата → время → имя |
| Мои записи | Активные записи, детали, отмена |
| Услуги и цены | Список услуг |
| Портфолио | Галерея работ с навигацией |
| Контакты | Адрес, телефон, часы работы, соц.сети |
| О мастере | Имя, опыт, описание |

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/me` | Профиль и активные записи |
| `/cancel` | Отменить запись |
| `/help` | Справка |
| `/admin` | Панель администратора (только для ADMIN_IDS) |

## Админ-панель

Доступна по команде `/admin` для пользователей, чьи Telegram ID указаны в `ADMIN_IDS`.

- **Записи** — просмотр активных записей с пагинацией, отмена/завершение.
- **Услуги** — добавление, редактирование, удаление услуг, цен и длительности.
- **Блокировки времени** — unavailable periods: полный день, один слот или диапазон времени без удаления существующих записей.
- **Портфолио** — добавление фото (с подписью) и удаление.
- **Соц.сети** — добавление/удаление ссылок (отображаются в Контактах и под Портфолио).
- **Настройки** — название студии, имя мастера, описание, опыт, адрес, телефон, часы работы.
- **Экспорт CSV** — выгрузка записей без лишних PII: без Telegram ID, username, телефона, имени и комментариев.

### Как добавить фото в портфолио

1. `/admin` → Портфолио → Добавить фото.
2. Отправьте фото в чат. Можно добавить подпись — она будет отображаться под фото в галерее.

### Как добавить социальные сети

1. `/admin` → Соц.сети → Добавить ссылку.
2. Отправьте сообщение в формате: `Instagram, https://instagram.com/nail_master`
3. Ссылка появится в разделе «Контакты» и под галереей портфолио как кнопка-ссылка.

### Услуги с длительностью

Формат добавления или редактирования услуги: `Название, цена, длительность_мин`.

Пример: `Маникюр классический, 3000, 60`.

Длительность должна быть положительной и кратной 30 минутам. Бот показывает только те стартовые слоты, где вся услуга помещается подряд до конца рабочего дня и не пересекает активные записи, временные locks или блокировки времени.

### Блокировки времени

Админ-панель → **Блокировки времени** поддерживает три формата:

```text
2026-12-07
2026-12-07, 10:00
2026-12-07, 10:00, 12:00, причина
```

Первый формат закрывает весь день, второй закрывает один 30-минутный слот, третий закрывает диапазон. Блокировка влияет только на будущую доступность и не отменяет уже существующие записи.

## Переменные окружения

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `BOT_TOKEN` | ✅ | Токен из @BotFather |
| `ADMIN_IDS` | ✅ | Telegram ID админа(ов), через запятую |
| `DATABASE_URL` | ❌ | PostgreSQL URL (без него — SQLite) |
| `POSTGRES_PASSWORD` | ❌ | Пароль bundled PostgreSQL в `docker-compose.yml`; должен совпадать с `DATABASE_URL` |
| `DB_PATH` | ❌ | Путь SQLite (ум. `/app/data/nailshop.db`) |
| `TIMEZONE` | ❌ | Часовой пояс (ум. `Asia/Almaty`) |
| `APP_ENV` | ❌ | `development` или `production` |
| `BOT_MODE` | ❌ | `polling` или `webhook` |
| `WEBHOOK_URL` | ❌ | Public HTTPS URL для webhook mode |
| `WEBHOOK_PATH` | ❌ | Путь webhook endpoint (ум. `/webhook`) |
| `SECRET_TOKEN` | ❌ | Обязателен при `APP_ENV=production` + `BOT_MODE=webhook` |
| `REDIS_URL` | ❌ | Redis URL для FSM-хранилища |
| `REQUIRE_REDIS_FSM` | ❌ | Требовать Redis FSM; рекомендуется `true` для multi-instance production |
| `MIN_BOOKING_ADVANCE_MINUTES` | ❌ | Мин. минут до записи (ум. 60) |
| `PROXY_URL` | ❌ | HTTP-прокси, если Telegram заблокирован |
| `SCHEDULER_LOCK_TTL_SECONDS` | ❌ | TTL DB-lock для scheduler в multi-instance режиме (ум. 120) |
| `S3_ENDPOINT_URL` | ❌ | S3-compatible endpoint для offsite backup |
| `S3_BUCKET` | ❌ | Bucket для offsite backup |
| `S3_ACCESS_KEY_ID` | ❌ | Access key для S3 upload |
| `S3_SECRET_ACCESS_KEY` | ❌ | Secret key для S3 upload |
| `S3_REGION` | ❌ | Region для S3 client (ум. `us-east-1`) |
| `S3_BACKUP_PREFIX` | ❌ | Prefix/object folder для backup-файлов |
| `POSTGRES_RESTORE_TEST_DATABASE_URL` | ❌ | Disposable PostgreSQL DB для real restore drill; никогда не production DB |
| `PRIVACY_RETENTION_DAYS` | ❌ | Через сколько дней anonymize старые completed/cancelled записи (ум. 365) |
| `ADMIN_AUDIT_RETENTION_DAYS` | ❌ | Через сколько дней удалять audit log (ум. 365) |

## Scheduler Lock

Scheduler jobs защищены DB-backed lock в таблице `scheduler_locks`.

- Каждый инстанс получает owner token при старте процесса.
- Перед persisted job (`reminder_24h`, `reminder_2h`, `auto_complete`, `review`) инстанс пытается взять lock `scheduler_job:<job_id>`.
- Если lock занят и не истёк, job пропускается на этом инстансе.
- После успешного выполнения persisted job удаляется из `scheduler_jobs`, поэтому второй инстанс не отправляет дубль даже если стартовал позже.
- Если инстанс упал, lock истекает по `SCHEDULER_LOCK_TTL_SECONDS`.
- Periodic jobs (`cleanup`, `backup`, digest) также обёрнуты lock и пропускают tick при занятом lock.

## Backup And Restore-Check

Локальный backup остаётся включённым всегда.

- SQLite backup создаётся как `backups/nailshop_YYYYMMDD_HHMMSS.db.gz`.
- PostgreSQL backup создаётся как gzip SQL dump через `pg_dump`.
- Если S3 env не заполнены полностью, backup работает local-only и не падает.
- Если S3 env заполнены, файл загружается через boto3 в `S3_BUCKET/S3_BACKUP_PREFIX/<filename>`.
- Ежедневный scheduler backup после создания файла запускает `restore_check`.
- Restore drill вручную: `python backup.py --restore-check`, `python backup.py --restore-check backups/<file>.db.gz` или `python restore_check.py backups/<file>.db.gz`.
- Для SQLite restore-check распаковывает backup во временную БД и запускает `PRAGMA integrity_check`.
- Для PostgreSQL SQL dump restore-check без `POSTGRES_RESTORE_TEST_DATABASE_URL` делает только structural check и явно пишет, что real restore skipped.
- Для полноценного PostgreSQL restore drill задайте disposable DB в `POSTGRES_RESTORE_TEST_DATABASE_URL`; check применит dump через `psql`, проверит critical tables, `booking_slots`, `unavailable_periods` и unique constraint.

## Privacy And Retention

PII controls доступны как функции storage и admin-команды:

- `/privacy_export <telegram_id>` — отправляет JSON export данных клиента администратору.
- `/privacy_delete <telegram_id>` — anonymize/delete PII по Telegram ID.
- `storage.export_client_data(telegram_id)` — user, bookings, waitlist, loyalty, reviews, referral counts.
- `storage.anonymize_client_data(telegram_id)` — удаляет user/loyalty rows, anonymize bookings/reviews/waitlist/referrals.
- Финансово и операционно важные booking records не hard-delete: сохраняются дата, время, услуга, цена, статус, но `telegram_id`, имя, username и comment очищаются.
- `storage.apply_retention_policy()` anonymize старые completed/cancelled записи и удаляет старый admin audit log.
- CSV export использует явный non-PII набор колонок.

## Webhook Checklist

Для webhook deploy:

- `BOT_MODE=webhook`.
- `WEBHOOK_URL=https://public-domain.example` без query/fragment.
- В `APP_ENV=production` обязательно задайте `SECRET_TOKEN`.
- Reverse proxy должен отдавать HTTPS/TLS и проксировать `WEBHOOK_PATH` на `WEBHOOK_HOST:WEBHOOK_PORT`.
- Проверьте `/health` и `/ready`; ответ включает DB availability, scheduler status, scheduler lock status и counters.
- Для нескольких инстансов используйте общий PostgreSQL/SQLite volume, общий Redis FSM и включённый DB scheduler lock.

## Observability

Без отдельного monitoring stack доступны:

- Structured log events для scheduler, backup, booking и payment placeholder.
- `/health` и `/ready` в webhook mode.
- In-process counters: `bookings_created`, `bookings_cancelled`, `reminders_sent`, `backup_success`, `backup_failed`.

## Инструкции для разработчика

### Локальное окружение

```powershell
py -m venv .venv-test
.\.venv-test\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-test.txt
```

### Тесты и проверки

```powershell
py -m pytest --cov -v
py -m compileall bot.py config.py storage.py handlers scheduler.py backup.py restore_check.py healthcheck.py
python -c "import bot; import restore_check; import healthcheck"
```

### Docker

```powershell
docker build --no-cache -t nailshop-bot:prod-ready .
docker compose build --no-cache
docker compose up -d
docker compose exec bot python healthcheck.py
```

### PostgreSQL integration tests

Используйте disposable PostgreSQL database, не production DB.

```powershell
$env:POSTGRES_TEST_DATABASE_URL="postgresql://nailshop:changeme@localhost:55432/nailshop_integration_test"
py -m pytest tests/test_production_hardening.py -k "postgres_integration" -v
```

### Миграции

Схема обновляется idempotent-миграциями в `storage.init_db()`. Для ручного применения используйте:

```powershell
python migrate_db.py
```

## Структура проекта

```
nailshop_deploy/
├── bot.py              # Точка входа
├── config.py           # Константы и настройки бренда
├── db.py               # Абстракция БД (SQLite/PG)
├── storage.py          # Операции с базой данных
├── keyboards.py        # Инлайн-клавиатуры
├── messages.py         # Текстовые шаблоны
├── scheduler.py        # Напоминания, авто-завершение броней
├── middleware.py       # Rate limit + Admin check
├── handlers/
│   ├── start.py        # /start, профиль, отмены
│   ├── booking.py      # Флоу записи
│   ├── info.py         # Контакты, цены, портфолио, о мастере
│   └── admin.py        # Админ-панель
├── tests/              # Тесты
├── .env.example        # Шаблон переменных окружения
├── Dockerfile
└── docker-compose.yml
```

## Деплой на Railway

1. Создайте новый проект на [railway.app](https://railway.app).
2. Подключите PostgreSQL сервис.
3. Добавьте переменные окружения:
   - `BOT_TOKEN`
   - `ADMIN_IDS`
   - `TIMEZONE`
   - `REDIS_URL` (рекомендуется)
4. Деплойтесь через GitHub.

## Примечания

- Бот рассчитан на работу одного мастера. Уведомления о записях и отменах приходят пользователям из `ADMIN_IDS`.
- Все настройки бренда (название, имя мастера, адрес, услуги) можно менять через админ-панель; они сохраняются в базе данных.
- Схема БД обновляется idempotent-миграциями в `storage.init_db()`: новые колонки и таблицы добавляются без отдельного ручного шага.
