# Аудит проекта nailshop_deploy

## Дата аудита
2026-07-05 (обновлён 2026-07-28)

## Стек проекта
- **Язык:** Python 3.10+
- **Фреймворк бота:** aiogram 3.x (3.7.0)
- **База данных:** SQLite (dev) / PostgreSQL (prod), абстракция в `db.py`
- **FSM-хранилище:** FileStorage (local) / RedisStorage (prod)
- **Планировщик:** APScheduler 3.10.4
- **Хранение файлов:** физические файлы не используются; портфолио ещё не реализовано
- **Деплой:** Docker / Docker Compose / Railway

## Структура проекта
- `bot.py` — точка входа, инициализация бота, middleware, роутеров, команд
- `config.py` — глобальные константы и настройки, загрузка/сохранение в БД
- `db.py` — абстракция БД (SQLite/PostgreSQL)
- `storage.py` — операции с БД: бронирования, пользователи, мастера, услуги, лояльность и т.д.
- `keyboards.py` — инлайн-клавиатуры
- `messages.py` — текстовые шаблоны
- `handlers/start.py` — `/start`, профиль, отмены, рефералы
- `handlers/booking.py` — флоу записи
- `handlers/info.py` — мастера, контакты, цены, портфолио-заглушка
- `handlers/admin.py` — админ-панель
- `utils.py` — отправка сообщений, уведомления админов/мастеров

## Найденная мультимастерная логика

### config.py
- `MASTER_IDS: dict = {}` — карта {master_name: telegram_id} для уведомлений нескольких мастеров.
- `MASTERS = {MASTER_NAME: {...}}` — структура под нескольких мастеров, хотя фактически всегда один.

### storage.py
- Таблица `masters` с колонками `name`, `experience`, `specialization`, `work_days`, `services`.
- Таблица `master_service_prices` — персональные цены по мастерам.
- Функции `save_master`, `remove_master`, `get_master_services`, `get_all_master_service_prices`, `get_effective_price`, `get_master_work_days`, `get_master_stats`, `get_stats_by_master`, `set_master_tg_id`, `update_master_work_days`, `update_master_services`.

### keyboards.py
- `masters_kb()` — клавиатура выбора мастера.
- `admin_masters_kb()`, `admin_master_detail_kb()` — управление несколькими мастерами.
- `services_kb(master_name: str)` — фильтрует услуги по мастеру и использует `master_service_prices`.

### handlers/start.py
- `/master` и `cb_masters` — вывод списка мастеров.
- `cb_master_detail` — детальная карточка мастера.
- Везде в текстах присутствует упоминание "масcтер".

### handlers/booking.py
- FSM `BookingStates.choose_master` — состояние выбора мастера.
- `cb_choose_master` — обработчик выбора мастера.
- `_get_next_dates(master_name)` — получает рабочие дни конкретного мастера.
- В `cb_book` уже есть автовыбор `config.MASTER_NAME`, но оставлены обработчики и клавиатуры под несколько мастеров.

### handlers/admin.py
- Разделы управления мастерами: `admin_masters`, `admin_add_master`, `admin_edit_master`, `admin_remove_master`, `master_schedule`, `master_services`, `master_prices`, `admin_set_master_tg`.
- Статистика по мастерам (`get_stats_by_master`).

### utils.py
- `notify_master(bot, master_name, ...)` — уведомление конкретному мастеру по имени.

## Мёртвый / неиспользуемый / тестовый код

- `emoji_config.py` — импортирует `P`, но нет уверенности, что все Premium-emoji корректно работают без подписки.
- `handlers/info.py`: `cb_portfolio` — заглушка "портфолио скоро появится", не реализовано.
- ~~`handlers/booking.py`: `cb_confirm_deprecated` — устаревший обработчик~~ ✅ **Удалён (2026-07-28)**
- `backup.py`, `monitoring.py`, `scheduler.py` — поддерживают лояльность/рефералы/напоминания, часть из которых не нужна соло-мастеру.
- Несколько дублирующих тестов (`test_handlers_booking2..5`, `test_handlers_admin2..6`) — вероятно, ослабленные/дублирующие.

## Обработка ошибок и валидация

### Уже хорошо
- Валидация имени: только буквы, пробелы, дефисы, апострофы, длина до 50.
- Валидация даты: нельзя выбрать прошедшую, нельзя более чем на 60 дней вперёд.
- Проверка занятости слота перед сохранением.
- FSM-guard на ключевых шагах.
- Rate-limit на запись.

### Проблемы
- В `cb_choose_service` callback может быть цифровым индексом — устаревшая логика, может привести к путанице.
- ~~В `cmd_help` инструкция всё ещё содержит "Выберите нейл-мастера"~~ ✅ **Исправлено на "О мастере" (2026-07-28)**
- ~~`bot.py`: опечатки в текстах ошибок ("Попрбуйте", "поже")~~ ✅ **Уже исправлены в предыдущих итерациях**
- Телефон: `+7 700 123 45 67` — валидация не очень строгая, допускает `+77001234567` и `87001234567`.

## Хранение фото / портфолио
- В текущем коде портфолио отсутствует.
- Фото не сохраняются.
- Социальные сети хранятся только как текст в `settings` (`instagram`, `telegram_contact`).

## Тесты
- Большое количество тестов, но много дублей (`test_handlers_booking2-5`, `test_handlers_admin2-6`).
- Необходимо проверить, не ослаблены ли assert-ы.
- Тестов на портфолио нет.

## Выводы и план действий
1. Удалить/упростить мультимастерную логику: убрать таблицу `masters`, `master_service_prices`, `MASTER_IDS`, `MASTERS`.
2. Пересобрать главное меню: Запись, Услуги и цены, Портфолио, Контакты/Соц.сети, Мои записи.
3. Сделать компактные inline-клавиатуры (2-3 кнопки в ряд).
4. Убрать экран выбора мастера из флоу записи.
5. Заменить тексты на плейсхолдеры `{MASTER_NAME}` / `{SALON_NAME}`.
6. Реализовать портфолио: таблицы `portfolio_photos` и `social_links`, админ-команды, просмотр клиентом.
7. Обновить `README.md` и `.env.example`.
8. Написать/обновить тесты и собрать zip-архив.
