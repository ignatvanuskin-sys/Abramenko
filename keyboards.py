# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import messages
import config
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from emoji_config import icon_button
from config import SERVICES
# Button text: plain text ONLY — no emojis (neither Unicode nor premium tg-emoji).
# Telegram inline button text is plain text, HTML tags are not rendered.

def _safe_cb(prefix: str, value: str, max_bytes: int = 62) -> str:
    """Truncate callback_data so prefix+value fits within Telegram 64-byte limit."""
    available = max_bytes - len(prefix.encode('utf-8'))
    if available <= 0:
        return prefix[:max_bytes]
    encoded = value.encode('utf-8')
    if len(encoded) <= available:
        return prefix + value
    truncated = encoded[:available].decode('utf-8', errors='ignore')
    return prefix + truncated


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [icon_button("Записаться", "calendar", callback_data="demo_book")],
        [icon_button("Услуги и цены", "money", callback_data="demo_faq:services"),
         icon_button("Цены", "money", callback_data="demo_faq:prices")],
        [icon_button("Адреса", "location", callback_data="demo_faq:addresses"),
         icon_button("График", "clock", callback_data="demo_faq:hours")],
        [icon_button("Портфолио", "media", callback_data="demo_faq:portfolio"),
         icon_button("Контакты", "phone", callback_data="demo_faq:contacts")],
        [icon_button("О студии", "info", callback_data="demo_faq:about")],
        [icon_button("Стать моделью", "people", callback_data="demo_lead:model")],
        [icon_button("Вакансии", "people", callback_data="demo_lead:vacancy")],
        [icon_button("Курс «Колорист с нуля»", "write", callback_data="demo_lead:course")],
    ])


async def services_kb(back: str = "main_menu") -> InlineKeyboardMarkup:
    """Service selection — only service name (no price, doesn't fit)."""
    service_list = list(config.SERVICES.keys())
    buttons = []
    for i in range(0, len(service_list), 2):
        row = []
        for name in service_list[i:i+2]:
            display = (name[:18] + "…") if len(name.encode("utf-8")) > 38 else name
            row.append(icon_button(
                text=display,
                callback_data=_safe_cb("service:", name),
            ))
        buttons.append(row)
    buttons.append([icon_button(text="Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_date(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        weekday = messages.WEEKDAYS[d.weekday()]
        month = messages.MONTHS[d.month - 1]
        return f"{weekday}, {d.day} {month}"
    except Exception:
        return date_str


def dates_kb(dates: list[str], back: str = "back_to_service") -> InlineKeyboardMarkup:
    buttons = []
    for i in range(0, len(dates), 2):
        row = []
        for d in dates[i:i+2]:
            row.append(icon_button(text=_format_date(d), callback_data=f"date:{d}"))
        buttons.append(row)
    buttons.append([icon_button(text="Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def time_slots_kb(
    slots: dict[str, str],
    back: str = "back_to_date",
    *,
    allow_waitlist: bool = True,
    cancel: str | None = None,
) -> InlineKeyboardMarkup:
    buttons = []
    free_slots = [(time_str, status) for time_str, status in slots.items() if status == "free"]
    if not free_slots:
        rows = [[icon_button(text="Нет свободных слотов", callback_data="no_slots")]]
        if allow_waitlist:
            rows.append([icon_button(text="Лист ожидания", callback_data="go_to_waitlist")])
        rows.append([icon_button(text="Назад", callback_data=back)])
        if cancel:
            rows.append([icon_button(text="Отмена", callback_data=cancel)])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    for i in range(0, len(free_slots), 4):
        row = []
        for time_str, status in free_slots[i:i+4]:
            row.append(icon_button(text=time_str, callback_data=f"time:{time_str}"))
        buttons.append(row)
    buttons.append([icon_button(text="Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [icon_button(text="Назад в меню", callback_data="main_menu")],
    ])


def booking_success_kb(booking_id: str = None) -> InlineKeyboardMarkup:
    buttons = []
    if booking_id:
        buttons.append([
            icon_button(text="Отменить", callback_data=f"ask_cancel:{booking_id}"),
        ])
    buttons.append([icon_button(text="Назад в меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def bookings_list_kb(bookings: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for b in bookings:
        date_str = _format_date(b["date"])
        buttons.append([icon_button(
            text=f"{date_str} {b['time']} — {b['service']}",
            callback_data=f"booking_detail:{b['id']}"
        )])
    buttons.append([icon_button(text="Назад в меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def booking_detail_kb(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            icon_button(text="Отменить", callback_data=f"ask_cancel:{booking_id}"),
        ],
        [icon_button(text="Назад в меню", callback_data="main_menu")],
    ])


def confirm_cancel_kb(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            icon_button(text="Да, отменить", callback_data=f"confirm_cancel:{booking_id}"),
            icon_button(text="Нет, вернуться", callback_data=f"booking_detail:{booking_id}"),
        ],
    ])


def phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Поделиться номером", request_contact=True)],
    ], resize_keyboard=True, one_time_keyboard=True)


def review_kb(booking_id: str) -> InlineKeyboardMarkup:
    labels = ["1", "2", "3", "4", "5"]
    row = [icon_button(text=label, callback_data=f"review:{booking_id}:{i}")
           for i, label in enumerate(labels, start=1)]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def remind_kb(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            icon_button(text="Приду", callback_data=f"remind_confirm:{booking_id}"),
            icon_button(text="Отменить", callback_data=f"remind_cancel:{booking_id}"),
        ],
    ])


def remind_cancel_kb(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [icon_button(text="Отменить запись", callback_data=f"remind_cancel:{booking_id}")],
    ])


def remind_2h_kb(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [icon_button(text="Буду!", callback_data=f"remind_confirm:{booking_id}")],
    ])


def skip_comment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [icon_button(text="Пропустить", callback_data="skip_comment")],
    ])


def cancel_bookings_kb(bookings: list) -> InlineKeyboardMarkup:
    buttons = []
    for b in bookings:
        date_str = _format_date(b['date'])
        label = f"{date_str} {b['time']} — {b['service'][:12]}"
        buttons.append([icon_button(text=label, callback_data=f"cancel_book:{b['id']}")])
    buttons.append([icon_button(text="Назад в меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def portfolio_kb(photo_id: int, has_prev: bool, has_next: bool, links: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    nav_row = []
    if has_prev:
        nav_row.append(icon_button(text="< Назад", callback_data=f"portfolio_page:{photo_id - 1}"))
    if has_next:
        nav_row.append(icon_button(text="Далее >", callback_data=f"portfolio_page:{photo_id + 1}"))
    if nav_row:
        rows.append(nav_row)

    for i in range(0, len(links), 2):
        row = []
        for link in links[i:i+2]:
            row.append(icon_button(text=link["platform"], url=link["url"]))
        rows.append(row)

    rows.append([icon_button(text="Назад в меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def portfolio_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            icon_button(text="Добавить фото", callback_data="admin_add_portfolio_photo"),
            icon_button(text="Удалить фото", callback_data="admin_delete_portfolio_photo"),
        ],
        [icon_button(text="Назад", callback_data="admin")],
    ])


def portfolio_delete_list_kb(photos: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for p in photos:
        caption = p.get("caption") or "без подписи"
        caption = caption[:30] + "…" if len(caption) > 30 else caption
        buttons.append([icon_button(
            text=f"#{p['id']} — {caption}",
            callback_data=f"admin_confirm_delete_photo:{p['id']}"
        )])
    buttons.append([icon_button(text="Назад", callback_data="admin_portfolio")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def social_links_admin_kb(links: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for link in links:
        platform = link["platform"][:20]
        buttons.append([icon_button(
            text=f"{platform}",
            callback_data=f"admin_delete_social_link:{link['id']}"
        )])
    buttons.append([
        icon_button(text="Добавить", callback_data="admin_add_social_link"),
        icon_button(text="Назад", callback_data="admin"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_delete_photo_kb(photo_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            icon_button(text="Да, удалить", callback_data=f"admin_delete_photo_confirm:{photo_id}"),
            icon_button(text="Отмена", callback_data="admin_portfolio"),
        ],
    ])


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            icon_button(text="Записи", callback_data="admin_bookings"),
            icon_button(text="Услуги", callback_data="admin_services"),
        ],
        [
            icon_button(text="Портфолио", callback_data="admin_portfolio"),
            icon_button(text="Соц.сети", callback_data="admin_social_links"),
        ],
        [
            icon_button(text="Настройки", callback_data="admin_settings"),
            icon_button(text="Экспорт CSV", callback_data="admin_export"),
        ],
        [
            icon_button(text="Статистика", callback_data="admin_stats"),
            icon_button(text="Рассылка", callback_data="admin_broadcast"),
        ],
        [icon_button(text="Аудит", callback_data="admin_audit")],
        [icon_button(text="Блокировки", callback_data="admin_unavailable")],
        [icon_button(text="Назад в меню", callback_data="main_menu")],
    ])


def admin_services_kb() -> InlineKeyboardMarkup:
    buttons = []
    service_items = list(config.SERVICES.items())
    for i in range(0, len(service_items), 2):
        row = []
        for service, price in service_items[i:i+2]:
            display = service[:18] + "…" if len(service.encode("utf-8")) > 38 else service
            row.append(icon_button(
                text=display,
                callback_data=f"admin_service_detail:{service}"
            ))
        buttons.append(row)
    buttons.append([
        icon_button(text="Добавить услугу", callback_data="admin_add_service"),
        icon_button(text="Назад", callback_data="admin"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_service_detail_kb(name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            icon_button(text="Редактировать", callback_data=_safe_cb("admin_edit_service:", name)),
            icon_button(text="Удалить", callback_data=_safe_cb("admin_remove_service:", name)),
        ],
        [icon_button(text="Назад", callback_data="admin_services")],
    ])


def admin_unavailable_kb(periods: list[dict]) -> InlineKeyboardMarkup:
    rows = [[icon_button(text="Добавить блокировку", callback_data="admin_add_unavailable")]]
    for period in periods[:10]:
        label = f"Удалить #{period['id']}"
        rows.append([icon_button(text=label[:40], callback_data=f"admin_delete_unavailable:{period['id']}")])
    rows.append([icon_button(text="Назад", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            icon_button(text="Адрес", callback_data="admin_change_address"),
            icon_button(text="Телефон", callback_data="admin_change_phone"),
        ],
        [
            icon_button(text="Часы работы", callback_data="admin_change_hours"),
            icon_button(text="Название", callback_data="admin_change_salon_name"),
        ],
        [
            icon_button(text="Имя мастера", callback_data="admin_change_master_name"),
            icon_button(text="Описание", callback_data="admin_change_master_desc"),
        ],
        [
            icon_button(text="Опыт", callback_data="admin_change_master_exp"),
            icon_button(text="Назад", callback_data="admin"),
        ],
    ])


def admin_cancel_booking_kb(booking_id: str, telegram_id: int | None = None, user_blocked: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            icon_button(text="Отменить", callback_data=f"admin_pre_cancel:{booking_id}"),
            icon_button(text="Завершить", callback_data=f"admin_complete_booking:{booking_id}"),
        ],
    ]
    if telegram_id:
        action = "unblock" if user_blocked else "block"
        text = "Разблокировать" if user_blocked else "Заблокировать"
        rows.append([icon_button(text=text, callback_data=f"admin_user_block:{telegram_id}:{action}:{booking_id}")])
    rows.append([icon_button(text="Назад", callback_data="admin_bookings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
