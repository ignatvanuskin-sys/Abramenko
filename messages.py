# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import html as html_module
from emoji_config import E


def welcome_text(name: str) -> str:
    """Генерирует приветственное сообщение с экранированием имени"""
    return (
        f"{E.BARBER} <b>Добро пожаловать в {html_module.escape(name)}!</b>\n\n"
        "Помогу собрать заявку на услуги студии, обучение или сотрудничество.\n\n"
        "Точное время подтверждает администратор. Выберите действие:"
    )


MAIN_MENU_TEXT = f"{E.HOME} <b>Главное меню</b>\n\nВыберите действие:"

# ===== ЗАПИСЬ =====

CHOOSE_SERVICE = (
    f"{E.LIST} <b>Шаг 1 из 3 — Выбор услуги</b>\n\n"
    "Выберите услугу:"
)


def service_selected(service_name: str, price: int, duration_minutes: int | None = None) -> str:
    duration_line = ""
    if duration_minutes:
        duration_line = f"{E.CLOCK} Длительность: {duration_minutes} мин\n"
    return (
        f"{E.CHECK} <b>Выбрана:</b> {html_module.escape(service_name)}\n"
        f"{E.MONEY} {price:,} ₸\n\n"
        f"{duration_line}"
        f"<b>Шаг 2 из 3 — Выбор даты</b>\n\n"
        "Выберите удобный день:"
    )


CHOOSE_DATE = (
    f"{E.CALENDAR} <b>Шаг 2 из 3 — Дата</b>\n\n"
    "Выберите удобный день:"
)


def date_selected(date_formatted: str) -> str:
    return (
        f"{E.CHECK} <b>Выбрана дата:</b> {date_formatted}\n\n"
        f"<b>Шаг 3 из 3 — Выбор времени</b>\n\n"
        "Выберите свободный слот:"
    )


CHOOSE_TIME = (
    f"{E.CLOCK} <b>Шаг 3 из 3 — Время</b>\n\n"
    "Выберите свободный слот:"
)


ENTER_NAME = (
    f"{E.USER} <b>Ваше имя</b>\n\n"
    "Как к вам обращаться?"
)


BOOKING_CONFIRM = (
    f"{E.LIST} <b>Подтверждение записи</b>\n\n"
    f"{E.USER} <b>Имя:</b> {{name}}\n"
    f"{E.BARBER} <b>Услуга:</b> {{service}}\n"
    f"{E.CALENDAR} <b>Дата:</b> {{date}}\n"
    f"{E.CLOCK} <b>Время:</b> {{time}}\n"
    f"{E.MONEY} <b>Стоимость:</b> {{price}} ₸\n\n"
    "Всё верно?"
)


BOOKING_CONFIRMED = (
    f"{E.CHECK} <b>Запись подтверждена!</b>\n\n"
    f"{E.CALENDAR} {{date}} в {{time}}\n"
    f"{E.BARBER} {{service}} — {{price}} ₸\n\n"
    f"{E.LOCATION} {{address}}\n\n"
    "Напоминания:\n"
    "• За 24 часа до визита\n"
    "• За 2 часа до визита"
)


BOOKING_CANCELLED = (
    f"{E.CROSS} <b>Запись отменена</b>\n\n"
    "Будем рады видеть вас снова — записывайтесь в любое время."
)


SLOT_BUSY = (
    f"{E.EXCLAMATION} Это время уже занято.\n\n"
    "Выберите другой слот или встаньте в лист ожидания."
)


RATE_LIMIT = (
    f"{E.EXCLAMATION} <b>Превышен лимит попыток.</b>\n\n"
    "Попробуйте через 30 минут."
)


def max_bookings_reached_text() -> str:
    import config as _cfg
    return (
        f"{E.EXCLAMATION} <b>У вас уже {_cfg.MAX_ACTIVE_BOOKINGS} активных записей (максимум).</b>\n\n"
        "Просмотреть записи — «Мои записи».\n"
        "Отменить — нажмите «Мои записи» → выберите запись → «Отменить запись»."
    )


ONE_ACTIVE_BOOKING = max_bookings_reached_text()
NO_ACTIVE_BOOKING = "У вас нет активных записей."


CONTACTS = (
    f"{E.LOCATION} <b>Контакты</b>\n\n"
    f"{E.LOCATION} <b>Адрес:</b>\n{{address}}\n\n"
    f"{E.PHONE} <b>Телефон:</b>\n{{phone}}\n\n"
    f"{E.CLOCK} <b>Часы работы:</b>\n{{hours}}"
)


# ===== О МАСТЕРЕ =====

def about_master_text() -> str:
    import config as _cfg
    return (
        f"{E.ARTIST_WOMAN} <b>О мастере</b>\n\n"
        f"Имя: <b>{html_module.escape(_cfg.MASTER_NAME)}</b>\n"
        f"Опыт: <b>{html_module.escape(_cfg.MASTER_EXPERIENCE)}</b>\n\n"
        f"{html_module.escape(_cfg.MASTER_DESCRIPTION)}\n\n"
        f"{E.PHONE} {html_module.escape(_cfg.SALON_PHONE)}"
    )


# ===== ПОРТФОЛИО =====

def portfolio_empty_text() -> str:
    return (
        f"{E.CAMERA} <b>Портфолио</b>\n\n"
        "Здесь пока нет фото работ.\n"
        "Загляните позже или свяжитесь с мастером."
    )


def portfolio_caption(photo_index: int, total: int, caption: str = "") -> str:
    text = f"{E.CAMERA} <b>Портфолио</b> ({photo_index}/{total})"
    if caption:
        text += f"\n\n{html_module.escape(caption)}"
    return text


# ===== НАПОМИНАНИЯ =====

REMINDER_24H = (
    f"{E.CLOCK} <b>Напоминание о записи</b>\n\n"
    f"Завтра, {{date}}, в {{time}}\n"
    f"{E.BARBER} <b>Услуга:</b> {{service}}\n\n"
    "Подтвердите визит или отмените запись:"
)


REMINDER_2H = (
    f"{E.EXCLAMATION} <b>Скоро у вас запись!</b>\n\n"
    f"Дата: <b>{{date}}</b>, осталось <b>2 часа</b> — {{time}}\n"
    f"{E.BARBER} <b>Услуга:</b> {{service}}\n\n"
    "Подтвердите присутствие — мы вас ждём!\n\n"
    f"{E.INFO} <i>Если планы изменились — зайдите в «Мои записи».</i>"
)


CANCEL_LAST_MINUTE_ADMIN = (
    f"{E.EXCLAMATION} <b>Отмена за 2 часа — слот свободен!</b>\n\n"
    f"{E.USER} <b>Клиент:</b> {{name}}\n"
    f"{E.CALENDAR} <b>Дата:</b> {{date}}\n"
    f"{E.CLOCK} <b>Время:</b> {{time}}\n"
    f"{E.BARBER} <b>Услуга:</b> {{service}}\n\n"
    "Можно предложить это время другому клиенту."
)


REQUEST_REVIEW = (
    f"{E.STAR} <b>Как прошёл визит?</b>\n\n"
    f"{E.CALENDAR} <b>Дата:</b> {{date}}\n\n"
    "Оцените качество обслуживания:"
)


LOYALTY_REWARD = (
    f"{E.STAR} <b>Бонус лояльности!</b>\n\n"
    "Это ваш {visit}-й визит!\n\n"
    f"{E.STAR} <b>Скидка {{discount}}%</b> на следующую услугу — сообщите при записи."
)


REFERRAL_WELCOME = f"{E.PLUS} Вы пришли по реферальному коду {{code}}!"
REFERRAL_BONUS_MSG = f"{E.STAR} Начислено {{bonus}} бонусов за приглашение!"


WAITLIST_OFFER = (
    f"{E.STAR} <b>Слот освободился!</b>\n\n"
    f"{E.CALENDAR} <b>Дата:</b> {{date}}\n"
    f"{E.CLOCK} <b>Время:</b> {{time}}\n\n"
    "Хотите записаться?"
)


WAITLIST_ADDED = (
    f"{E.CHECK} <b>Вы в листе ожидания</b>\n\n"
    f"{E.CALENDAR} <b>Дата:</b> {{date}}\n"
    f"{E.CLOCK} <b>Время:</b> {{time}}\n\n"
    "Уведомим, если время освободится."
)


ERROR = (
    f"{E.CROSS} Что-то пошло не так.\n"
    f"{E.RELOAD} Попробуйте ещё раз или обратитесь к администратору."
)


CANCELLED_SUCCESS = (
    f"{E.CHECK} <b>Запись отменена</b>\n\n"
    f"{E.ID} ID: <code>{{booking_id}}</code>"
)

# ===== АДМИН =====

ADMIN_BOOKING_NOTIFY = (
    f"{E.PLUS} <b>Новая запись</b>\n\n"
    f"{E.USER} <b>Клиент:</b> {{name}}\n"
    f"{E.BARBER} <b>Услуга:</b> {{service}}\n"
    f"{E.CALENDAR} <b>Дата:</b> {{date}}\n"
    f"{E.CLOCK} <b>Время:</b> {{time}}\n"
    f"{E.MONEY} <b>Стоимость:</b> {{price:,}} ₸".replace(",", " ")
)

ADMIN_CANCEL_NOTIFY = (
    f"{E.CROSS} <b>Отмена записи</b>\n\n"
    f"{E.ID} ID: <code>{{booking_id}}</code>\n"
    "Отменена клиентом."
)

ADMIN_STATS = (
    f"{E.CHART} <b>Статистика</b>\n\n"
    f"{E.LIST} <b>Всего:</b> {{total}}\n"
    f"{E.CHECK} <b>Активных:</b> {{active}}\n"
    f"{E.CROSS} <b>Отменённых:</b> {{cancelled}}\n"
    f"{E.CHECK} <b>Завершённых:</b> {{completed}}\n\n"
    f"{E.MONEY} <b>Выручка:</b> {{revenue}} ₸"
)

ADMIN_EXPORT = f"{E.CHECK} Экспорт завершён. Файл: <code>{{filename}}</code>"
ADMIN_ONLY = "🔒 Команда доступна только администратору."


def get_about_text() -> str:
    import config as _cfg
    return (
        f"{E.BARBER} <b>{_cfg.SALON_NAME}</b>\n\n"
        f"{E.LOCATION} {_cfg.SALON_ADDRESS}\n"
        f"{E.PHONE} {_cfg.SALON_PHONE}\n"
        f"{E.CLOCK} {_cfg.SALON_WORKING_HOURS}"
    )


WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]

# ===== АДМИН CMS (plain text) =====
ADMIN_SERVICE_LIST = "Услуги:\n\n{services}"
ADMIN_SERVICE_ADDED = "Услуга {name} добавлена ({price} ₸)."
ADMIN_SERVICE_REMOVED = "Услуга {name} удалена."
ADMIN_SERVICE_UPDATED = "Услуга {name} обновлена ({price} ₸)."
ADMIN_SERVICE_NOT_FOUND = "Услуга {name} не найдена."
ADMIN_ADD_SERVICE_PROMPT = "Добавление услуги\n\nФормат: Название, цена, длительность_мин\nПример: Женская стрижка, 0, 60"
ADMIN_EDIT_SERVICE_PROMPT = "Редактирование услуги\n\nФормат: Название, цена, длительность_мин\nПример: Женская стрижка, 0, 60"
ADMIN_REMOVE_SERVICE_PROMPT = "Выберите услугу для удаления:"
ADMIN_SETTINGS = "Настройки\n\nАдрес: {address}\nТелефон: {phone}\nЧасы работы: {hours}\nМастер: {master}"
ADMIN_CHANGE_ADDRESS_PROMPT = "Введите новый адрес:"
ADMIN_CHANGE_PHONE_PROMPT = "Введите новый телефон:"
ADMIN_CHANGE_HOURS_PROMPT = "Часы работы (пример: Пн-Сб: 10:00-21:00, Вс: 11:00-19:00):"
ADMIN_CHANGE_SLOTS_PROMPT = "Слоты через запятую (10:00, 10:30, 11:00):"
ADMIN_SETTINGS_UPDATED = "Настройки обновлены."
ADMIN_STATS_DETAILED = (
    "Статистика\n\nВсего: {total}\nАктивных: {active}\n"
    "Отменённых: {cancelled}\nЗавершённых: {completed}\nВыручка: {revenue} ₸\n\n"
    "По дням:\n{by_day}\nПо услугам:\n{by_service}"
)
ADMIN_STATS_BY_DAY = "  • {date}: {count} записей\n"
ADMIN_STATS_BY_SERVICE = "  • {name}: {count} записей, {revenue} ₸\n"
ADMIN_BOOKING_CANCEL = "Запись {booking_id} отменена администратором."
ADMIN_BOOKING_COMPLETE = "Запись {booking_id} завершена."
ADMIN_BOOKING_LIST = "Записи:\n\n{bookings}"
ADMIN_BOOKING_LIST_LINE = "• {id}: {date} {time} — {name} ({service}) [{status}]\n"
ADMIN_WAITLIST = "Лист ожидания:\n\n{waitlist}"
ADMIN_WAITLIST_LINE = "• {name}: {date} {time} — {service} [{status}]\n"
ADMIN_LOYALTY_LIST = "Программа лояльности:\n\n{loyalty}"
ADMIN_LOYALTY_LINE = "• {name}: визитов {visits}, бонусов {bonuses}\n"
ADMIN_REVIEWS_LIST = "Отзывы:\n\n{reviews}"
ADMIN_REVIEWS_LINE = "• {booking_id}: {rating}/5 ({date})\n"
ADMIN_REFERRALS_LIST = "Рефералы:\n\n{referrals}"
ADMIN_REFERRALS_LINE = "• {referrer_id} → {referred_id} ({date})\n"

ADMIN_PORTFOLO_INTRO = (
    f"{E.CAMERA} <b>Управление портфолио</b>\n\n"
    "Добавляйте фото работ или удаляйте лишние."
)
ADMIN_PORTFOLIO_ADD_PROMPT = (
    f"{E.CAMERA} <b>Добавление фото</b>\n\n"
    "Отправьте фото. Можно добавить подпись к фото."
)
ADMIN_PORTFOLIO_DELETE_EMPTY = "В портфолио пока нет фото для удаления."
ADMIN_PORTFOLIO_DELETE_CONFIRM = "Удалить фото #{photo_id}? Это действие нельзя отменить."
ADMIN_PORTFOLIO_DELETED = "Фото #{photo_id} удалено."
ADMIN_SOCIAL_LINKS_INTRO = (
    f"{E.LINK} <b>Социальные сети</b>\n\n"
    "Добавляйте ссылки на Instagram, TikTok, WhatsApp и т.п."
)
ADMIN_SOCIAL_ADD_PROMPT = (
    "Добавление ссылки\n\n"
    "Формат: Название, URL\n"
    "Пример: Instagram, https://instagram.com/portfolio"
)
ADMIN_SOCIAL_LINK_DELETED = "Ссылка удалена."

# ===== КНОПКИ =====
BACK_BUTTON = f"{E.HOME} Назад"
CONFIRM_BUTTON = f"{E.CHECK} Подтвердить"
CANCEL_BUTTON = f"{E.CROSS} Отменить"
YES_BUTTON = f"{E.CHECK} Приду"
NO_BUTTON = f"{E.CROSS} Отменить запись"
PHONE_BUTTON = f"{E.MOBILE} Поделиться номером"
CALL_BUTTON = f"{E.PHONE} Позвонить"


# ===== РЕФЕРАЛЬНАЯ СИСТЕМА =====
INVITE_FRIEND = (
    f"{E.STAR} <b>Пригласи друга — получи бонусы!</b>\n\n"
    "Поделитесь вашей персональной ссылкой:\n"
    "<code>{link}</code>\n\n"
    f"{E.LIST} <b>Как это работает:</b>\n"
    "• Друг переходит по ссылке и открывает бота\n"
    "• Вы автоматически получаете <b>{bonus} бонусов</b>\n"
    "• Бонусы можно использовать при оплате услуги\n\n"
    f"{E.CHART} <b>Ваша статистика:</b>\n"
    "• Приглашено друзей: <b>{count}</b>\n"
    "• Текущий баланс бонусов: <b>{bonuses}</b>\n\n"
    f"{E.IDEA} <i>Ссылка уникальна для вас — она не меняется.</i>\n"
    "Поделитесь в чате, соцсетях или отправьте друзьям!"
)


INVITE_FRIEND_SHARE = (
    f"{E.BARBER} Приглашаю в <b>{{name}}</b>!\n\n"
    f"{E.ARTIST_WOMAN} Услуги для волос и удобная заявка через Telegram.\n"
    "Точное время подтвердит администратор.\n\n"
    "{link}"
)
