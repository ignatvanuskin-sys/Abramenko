from emoji_config import icon_button
# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Validated, persist-first FSM requests for Abramenko Studio."""

import hashlib
import html
import json
import logging
import re
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
import demo_repository
from studio_data import BRANCHES, COLORING_SERVICES, FAQ, SERVICES

router = Router()
logger = logging.getLogger(__name__)
PHONE_RE = re.compile(r"^\+7\d{10}$")
# Full, unambiguous DD.MM.YYYY dates only — a bare DD.MM cannot be validated
# against "future" and would make the admin card ambiguous.
DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
# Bounded free-text fields: keeps the admin card inside Telegram's message
# limit and prevents oversized-input abuse (leads are already capped at 500).
MAX_TEXT_FIELD = 200


def kb(items, prefix, extra=None):
    rows = [[icon_button(text=x, callback_data=f"{prefix}:{i}")] for i, x in enumerate(items)]
    if extra:
        rows.append([icon_button(text=extra[0], callback_data=extra[1])])
    rows.append([icon_button(text="Отмена", callback_data="demo_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def normalize_phone(value: str) -> str | None:
    normalized = re.sub(r"[\s()-]", "", value or "")
    return normalized if PHONE_RE.fullmatch(normalized) else None


def valid_date(value: str) -> bool:
    if not DATE_RE.fullmatch(value or ""):
        return False
    try:
        parsed = datetime.strptime(value, "%d.%m.%Y")
        return parsed.date() >= datetime.now().date()
    except ValueError:
        return False


def admin_card(kind, data):
    value = lambda key: html.escape(str(data.get(key) or "—"))
    return (
        "<b>Новая заявка Abramenko Studio</b>\n"
        f"Тип: {html.escape(kind)}\nИмя: {value('name')}\nТелефон: {value('phone')}\n"
        f"Филиал: {value('branch')}\nДата: {value('date')}\nВремя: {value('time')}\n"
        f"Дополнительно: {value('additional')}\nИсточник: Telegram-демо\n"
        "Статус: требуется подтверждение"
    )


def normalized_payload(data: dict) -> dict:
    allowed = ("service", "branch", "date", "time", "name", "phone", "master", "hair_length",
               "desired_result", "last_coloring", "photo_ready", "experience", "portfolio",
               "level", "goal", "city", "format", "additional")
    return {key: data.get(key) for key in allowed}


def retry_kb(request_id: str) -> InlineKeyboardMarkup:
    # UUID request IDs keep this callback well below Telegram's 64-byte limit.
    return InlineKeyboardMarkup(inline_keyboard=[[icon_button(text="Повторить отправку", callback_data=f"demo_retry:{request_id}")]])


async def _retry_failure(message, request_id: str, *, booking_created: bool = False):
    prefix = "Запись уже создана. " if booking_created else ""
    await message.answer(
        prefix + "Заявка сохранена, но уведомление не отправлено. Нажмите «Повторить отправку» позже.",
        reply_markup=retry_kb(request_id),
    )


async def finish(message, state, kind, request_type):
    data = await state.get_data()
    payload = normalized_payload(data)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    key = hashlib.sha256(f"{message.from_user.id}:{request_type}:{canonical}".encode()).hexdigest()
    row = await demo_repository.create_or_get_request(request_type, message.from_user.id, payload, key)
    booking_created = bool(data.get("booking_id"))
    if row["notification_status"] == "sent":
        await state.clear()
        return await message.answer("Заявка уже сохранена и передана администратору.")
    admin_id = config.DEMO_ADMIN_CHAT_ID
    if admin_id is None:
        error = "ADMIN_CHAT_ID не настроен"
        await demo_repository.update_notification(row["id"], "failed", error)
        await state.clear()
        return await _retry_failure(message, row["id"], booking_created=booking_created)
    if not await demo_repository.claim_notification(row["id"]):
        await state.clear()
        return await message.answer("Заявка уже обрабатывается или передана администратору.")
    try:
        await message.bot.send_message(admin_id, admin_card(kind, payload), parse_mode="HTML")
    except Exception as exc:
        await demo_repository.update_notification(row["id"], "failed", str(exc)[:1000])
        await state.clear()
        return await _retry_failure(message, row["id"], booking_created=booking_created)
    # Telegram has no transactional send+DB primitive: a crash after send can cause
    # at-most-once delivery (or a retry can duplicate), so this is deliberately not
    # advertised as exactly-once. Failed claims remain retryable.
    try:
        await demo_repository.update_notification(row["id"], "sent")
    except Exception:
        # The notification was delivered but the status write failed. Never leave
        # the row stuck in "sending" (that would block any retry forever); fall
        # back to "failed" so the user can verify delivery, at the cost of a
        # possible duplicate on retry — the documented at-least-once tradeoff.
        logger.exception("Failed to record sent status for demo request %s", row["id"])
        try:
            await demo_repository.update_notification(row["id"], "failed", "sent but status not recorded")
        except Exception:
            logger.exception("Failed to mark demo request %s retryable", row["id"])
        await state.clear()
        return await message.answer("Заявка передана администратору. Если уведомление не пришло, напишите нам.")
    await state.clear()
    await message.answer("Спасибо! Заявка передана администратору для подтверждения.")


class Booking(StatesGroup):
    service = State(); branch = State(); date = State(); time = State(); name = State(); phone = State()
    master = State(); hair_length = State(); result = State(); last_coloring = State(); photo = State(); confirm = State()


class Lead(StatesGroup):
    fields = State(); confirm = State()


@router.callback_query(F.data == "legacy_demo_book")
async def start_booking(c: CallbackQuery, state: FSMContext):
    await state.clear(); await state.set_state(Booking.service)
    await c.message.edit_text("Выберите услугу:", reply_markup=kb(SERVICES, "demo_service")); await c.answer()


@router.callback_query(F.data.startswith("demo_service:"), Booking.service)
async def service(c: CallbackQuery, state: FSMContext):
    try: value = SERVICES[int(c.data.rsplit(":", 1)[1])]
    except (ValueError, IndexError): return await c.answer("Услуга не найдена", show_alert=True)
    await state.update_data(service=value); await state.set_state(Booking.branch)
    await c.message.edit_text("Выберите филиал:", reply_markup=kb(BRANCHES, "demo_branch")); await c.answer()


@router.callback_query(F.data.startswith("demo_branch:"), Booking.branch)
async def branch(c: CallbackQuery, state: FSMContext):
    try: value = BRANCHES[int(c.data.rsplit(":", 1)[1])]
    except (ValueError, IndexError): return await c.answer("Филиал не найден", show_alert=True)
    await state.update_data(branch=value); await state.set_state(Booking.date)
    await c.message.edit_text("Напишите желаемую дату: ДД.ММ.ГГГГ"); await c.answer()


@router.message(Booking.date)
async def date(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if not valid_date(value): return await m.answer("Введите корректную будущую дату: ДД.ММ.ГГГГ.")
    await state.update_data(date=value); await state.set_state(Booking.time); await m.answer("Напишите желаемое время в формате ЧЧ:ММ:")


@router.message(Booking.time)
async def time(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if not TIME_RE.fullmatch(value): return await m.answer("Введите корректное время в формате ЧЧ:ММ.")
    await state.update_data(time=value); await state.set_state(Booking.name); await m.answer("Как вас зовут?")


@router.message(Booking.name)
async def name(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if not value or len(value) > 80: return await m.answer("Пожалуйста, напишите имя до 80 символов.")
    await state.update_data(name=value); await state.set_state(Booking.phone); await m.answer("Ваш телефон в формате +7XXXXXXXXXX:")


@router.message(Booking.phone)
async def phone(m: Message, state: FSMContext):
    value = normalize_phone(m.text or "")
    if not value: return await m.answer("Нужен номер в формате +7XXXXXXXXXX.")
    await state.update_data(phone=value); await state.set_state(Booking.master)
    await m.answer("Есть пожелание по мастеру? Напишите имя или «нет».")


@router.message(Booking.master)
async def master(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if len(value) > MAX_TEXT_FIELD:
        return await m.answer(f"Пожалуйста, короче (максимум {MAX_TEXT_FIELD} символов).")
    await state.update_data(master=None if value.lower() in {"нет", "—", "-"} else value)
    data = await state.get_data()
    if data["service"] in COLORING_SERVICES:
        await state.set_state(Booking.hair_length); return await m.answer("Какая длина волос?")
    await show_booking_confirmation(m, state)


@router.message(Booking.hair_length)
async def hair_length(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if not value or len(value) > MAX_TEXT_FIELD:
        return await m.answer(f"Напишите длину короче (максимум {MAX_TEXT_FIELD} символов).")
    await state.update_data(hair_length=value); await state.set_state(Booking.result); await m.answer("Какой результат хотите получить?")


@router.message(Booking.result)
async def result(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if not value or len(value) > MAX_TEXT_FIELD:
        return await m.answer(f"Опишите результат короче (максимум {MAX_TEXT_FIELD} символов).")
    await state.update_data(desired_result=value); await state.set_state(Booking.last_coloring); await m.answer("Когда было последнее окрашивание?")


@router.message(Booking.last_coloring)
async def last_coloring(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if not value or len(value) > MAX_TEXT_FIELD:
        return await m.answer(f"Ответьте короче (максимум {MAX_TEXT_FIELD} символов).")
    await state.update_data(last_coloring=value); await state.set_state(Booking.photo); await m.answer("Готовы прислать фото волос при дневном свете? «да» или «нет».")


@router.message(Booking.photo)
async def photo(m: Message, state: FSMContext):
    value = (m.text or "").strip().lower()
    if value not in {"да", "нет"}: return await m.answer("Ответьте «да» или «нет».")
    await state.update_data(photo_ready=value); await show_booking_confirmation(m, state)


async def show_booking_confirmation(m: Message, state: FSMContext):
    data = await state.get_data()
    extra = []
    for label, key in (("Услуга", "service"), ("Мастер", "master"), ("Длина", "hair_length"), ("Результат", "desired_result"), ("Последнее окрашивание", "last_coloring"), ("Фото", "photo_ready")):
        if data.get(key): extra.append(f"{label}: {data[key]}")
    await state.update_data(additional="; ".join(extra)); await state.set_state(Booking.confirm)
    await m.answer("Проверьте данные. Отправить заявку?", reply_markup=kb(["Да, отправить"], "demo_confirm", ("Заполнить заново", "demo_book")))


@router.callback_query(F.data == "demo_confirm:0", Booking.confirm)
async def confirm(c: CallbackQuery, state: FSMContext):
    await finish(c.message, state, "Запись", "booking"); await c.answer()


LEADS = {
    "model": ("Стать моделью", [("name", "Ваше имя?"), ("phone", "Телефон +7XXXXXXXXXX?"), ("service", "Какая услуга интересует?"), ("portfolio", "Ссылка на портфолио или «нет»?"), ("branch", "Какой филиал? AIRTOUCH (Букетова, 61) или Мадам (Жамбыла, 127)?")]),
    "vacancy": ("Вакансия", [("name", "Ваше имя?"), ("experience", "Расскажите об опыте."), ("portfolio", "Ссылка на портфолио или «нет»?"), ("phone", "Телефон +7XXXXXXXXXX?"), ("branch", "Какой филиал? AIRTOUCH (Букетова, 61) или Мадам (Жамбыла, 127)?")]),
    "course": ("Курс «Колорист с нуля»", [("level", "Уровень: с нуля, есть база или повышение?"), ("goal", "Ваша цель обучения?"), ("city", "Ваш город?"), ("phone", "Телефон +7XXXXXXXXXX?"), ("format", "Формат: очно, онлайн или не выбрано?")]),
}


@router.callback_query(F.data.startswith("demo_lead:"))
async def lead_start(c: CallbackQuery, state: FSMContext):
    key = c.data.rsplit(":", 1)[1]
    if key not in LEADS: return await c.answer("Раздел не найден", show_alert=True)
    title, fields = LEADS[key]
    await state.clear(); await state.update_data(lead_key=key, lead_title=title, lead_fields=fields, lead_index=0); await state.set_state(Lead.fields)
    await c.message.edit_text(fields[0][1]); await c.answer()


@router.message(Lead.fields)
async def lead_field(m: Message, state: FSMContext):
    data = await state.get_data(); fields = data["lead_fields"]; index = data["lead_index"]; field = fields[index][0]; value = (m.text or "").strip()
    if not value: return await m.answer("Поле не должно быть пустым.")
    if len(value) > 500: return await m.answer("Слишком длинное значение (максимум 500 символов).")
    if field == "phone":
        value = normalize_phone(value)
        if not value: return await m.answer("Нужен номер в формате +7XXXXXXXXXX.")
    if field == "branch" and value.lower() not in {x.lower() for x in BRANCHES}:
        return await m.answer("Выберите филиал из списка: AIRTOUCH (Букетова, 61) или Мадам (Жамбыла, 127).")
    if field == "branch": value = next(x for x in BRANCHES if x.lower() == value.lower())
    if field == "name" and len(value) > 80: return await m.answer("Имя должно быть не длиннее 80 символов.")
    if field in {"experience", "portfolio", "level", "goal", "city", "format", "service"} and len(value) > MAX_TEXT_FIELD:
        return await m.answer(f"Пожалуйста, короче (максимум {MAX_TEXT_FIELD} символов).")
    await state.update_data(**{field: value}, lead_index=index + 1)
    if index + 1 < len(fields): return await m.answer(fields[index + 1][1])
    updated = await state.get_data(); omitted = {"lead_key", "lead_title", "lead_fields", "lead_index", "name", "phone", "branch", "date", "time"}
    await state.update_data(additional="; ".join(f"{k}: {v}" for k, v in updated.items() if k not in omitted and v)); await state.set_state(Lead.confirm)
    await m.answer("Проверьте данные. Отправить заявку?", reply_markup=kb(["Да, отправить"], "demo_lead_confirm", ("Заполнить заново", f"demo_lead:{updated['lead_key']}")))


@router.callback_query(F.data == "demo_lead_confirm:0", Lead.confirm)
async def lead_confirm(c: CallbackQuery, state: FSMContext):
    data = await state.get_data(); await finish(c.message, state, data["lead_title"], data["lead_key"]); await c.answer()


@router.callback_query(F.data.startswith("demo_retry:"))
async def retry(c: CallbackQuery):
    request_id = c.data.rsplit(":", 1)[1]
    row = await demo_repository.get_request(request_id)
    if not row or row["telegram_id"] != c.from_user.id:
        return await c.answer("Заявка не найдена", show_alert=True)
    if row["notification_status"] == "sent":
        return await c.answer("Заявка уже передана администратору.", show_alert=True)
    admin_id = config.DEMO_ADMIN_CHAT_ID
    if admin_id is None:
        return await c.answer("Уведомление недоступно: ADMIN_CHAT_ID не настроен.", show_alert=True)
    if not await demo_repository.claim_notification(request_id):
        return await c.answer("Уведомление уже обрабатывается или недоступно.", show_alert=True)
    try:
        await c.message.bot.send_message(admin_id, admin_card({"booking": "Запись", "model": "Стать моделью", "vacancy": "Вакансия", "course": "Курс «Колорист с нуля»"}.get(row["request_type"], row["request_type"]), row["payload"]), parse_mode="HTML")
    except Exception as exc:
        await demo_repository.update_notification(request_id, "failed", str(exc)[:1000])
        return await c.answer("Не отправлено. Попробуйте ещё раз.", show_alert=True)
    try:
        await demo_repository.update_notification(request_id, "sent")
    except Exception:
        logger.exception("Failed to record sent status on retry for request %s", request_id)
        try:
            await demo_repository.update_notification(request_id, "failed", "sent but status not recorded")
        except Exception:
            logger.exception("Failed to mark retried request %s retryable", request_id)
        return await c.answer("Отправлено. Если уведомление не пришло, напишите нам.", show_alert=True)
    await c.answer("Заявка передана администратору.", show_alert=True)


@router.callback_query(F.data.startswith("demo_faq:"))
async def faq(c: CallbackQuery):
    key = c.data.rsplit(":", 1)[1]
    text = FAQ.get(key, "Точную информацию уточнит администратор.")
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[icon_button(text="Назад", callback_data="main_menu")]])); await c.answer()


@router.callback_query(F.data == "demo_cancel")
async def cancel(c: CallbackQuery, state: FSMContext):
    await state.clear(); await c.message.edit_text("Заявка отменена. Нажмите /start, чтобы вернуться в меню."); await c.answer()
