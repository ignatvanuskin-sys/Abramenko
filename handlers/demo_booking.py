# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Button-driven Abramenko booking flow; lead and FAQ flows stay in handlers.demo."""

import html
import secrets
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove
from emoji_config import icon_button

import config
import keyboards
import storage
from handlers import booking as booking_engine
from handlers.demo import MAX_TEXT_FIELD, finish, normalize_phone
from studio_data import BRANCHES, COLORING_SERVICES, SERVICES
from tz_utils import get_now

router = Router()
MASTER = "Любой мастер"


class DemoBooking(StatesGroup):
    branch = State(); service = State(); date = State(); time = State(); name = State(); phone = State()
    hair_length = State(); result = State(); last_coloring = State(); photo = State(); confirm = State()


def _kb(items, prefix, back=None):
    rows = [[icon_button(text=value, callback_data=f"{prefix}:{index}")] for index, value in enumerate(items)]
    if back:
        rows.append([icon_button(text="Назад", callback_data=back)])
    rows.append([icon_button(text="Отмена", callback_data="demo_booking_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def resource(data):
    return f"{data.get('branch', '—')}|{MASTER}"


def normalize_contact_phone(value):
    value = (value or "").strip()
    if value.startswith("8") and len(value) == 11:
        value = "+7" + value[1:]
    elif value.startswith("7") and len(value) == 11:
        value = "+" + value
    return normalize_phone(value)


def demo_time_slots_kb(slots, back="db_back:date"):
    return keyboards.time_slots_kb(
        slots, back=back, allow_waitlist=False, cancel="demo_booking_cancel"
    )


def booking_recovery_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        icon_button(text="Восстановить заявку", callback_data="db_recover_request")
    ]])


async def release(user_id, state):
    data = await state.get_data()
    if data.get("date") and data.get("time"):
        await storage.release_slot_lock(data["date"], data["time"], resource(data), duration_minutes=data.get("duration_minutes"), owner_id=user_id, owner_token=data.get("lock_token"))


@router.callback_query(F.data == "demo_book")
async def start(c: CallbackQuery, state: FSMContext):
    await release(c.from_user.id, state); await state.clear(); await state.set_state(DemoBooking.branch)
    await c.message.edit_text("<b>Шаг 1 из 6 — выберите филиал</b>", reply_markup=_kb(BRANCHES, "db_branch"), parse_mode="HTML"); await c.answer()


@router.callback_query(F.data.startswith("db_branch:"), DemoBooking.branch)
async def choose_branch(c, state):
    try: value = BRANCHES[int(c.data.rsplit(":", 1)[1])]
    except (ValueError, IndexError): return await c.answer("Филиал не найден", show_alert=True)
    await state.update_data(branch=value, master=MASTER); await state.set_state(DemoBooking.service)
    await c.message.edit_text("<b>Шаг 2 из 6 — выберите услугу</b>", reply_markup=_kb(SERVICES, "db_service", "demo_book"), parse_mode="HTML"); await c.answer()


@router.callback_query(F.data.startswith("db_service:"), DemoBooking.service)
async def choose_service(c, state):
    try: value = SERVICES[int(c.data.rsplit(":", 1)[1])]
    except (ValueError, IndexError): return await c.answer("Услуга не найдена", show_alert=True)
    await state.update_data(service=value, duration_minutes=config.get_service_duration(value)); await state.set_state(DemoBooking.date)
    dates = await booking_engine._get_next_dates()
    await state.update_data(eligible_dates=list(dates))
    await c.message.edit_text("<b>Шаг 3 из 6 — выберите дату</b>", reply_markup=keyboards.dates_kb(dates, back="db_back:service"), parse_mode="HTML"); await c.answer()


@router.callback_query(F.data.startswith("date:"), DemoBooking.date)
async def choose_date(c, state):
    value = c.data.split(":", 1)[1]
    try: selected = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError: return await c.answer("Неверная дата", show_alert=True)
    if selected < get_now(config.TIMEZONE).date(): return await c.answer("Нельзя выбрать прошедшую дату", show_alert=True)
    data = await state.get_data()
    if value not in data.get("eligible_dates", []): return await c.answer("Дата больше недоступна. Выберите её заново.", show_alert=True)
    slots = await booking_engine._get_available_slots(value, resource(data), data.get("duration_minutes"))
    await state.update_data(date=value); await state.set_state(DemoBooking.time)
    await c.message.edit_text("<b>Шаг 4 из 6 — выберите время</b>", reply_markup=demo_time_slots_kb(slots), parse_mode="HTML"); await c.answer()


@router.callback_query(F.data.startswith("time:"), DemoBooking.time)
async def choose_time(c, state):
    value = c.data.split(":", 1)[1]; data = await state.get_data(); key = resource(data)
    slots = await booking_engine._get_available_slots(data["date"], key, data.get("duration_minutes"))
    if slots.get(value) != "free": return await c.answer("Этот слот уже занят.", show_alert=True)
    token = secrets.token_urlsafe(24)
    if not await storage.create_slot_lock(data["date"], value, key, duration_minutes=data.get("duration_minutes"), owner_id=c.from_user.id, owner_token=token):
        return await c.answer("Этот слот только что заняли.", show_alert=True)
    await state.update_data(time=value, lock_token=token); await state.set_state(DemoBooking.name)
    rows = []
    if c.from_user.first_name: rows.append([icon_button(text=f"Использовать «{c.from_user.first_name[:50]}»", callback_data="db_tg_name")])
    rows += [[icon_button(text="Назад", callback_data="db_back:time")], [icon_button(text="Отмена", callback_data="demo_booking_cancel")]]
    await c.message.edit_text("<b>Шаг 5 из 6 — как вас зовут?</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML"); await c.answer()


async def accept_name(value, target, state):
    value = value.strip()
    if not value or len(value) > 80: return False
    await state.update_data(name=value); await state.set_state(DemoBooking.phone)
    await target.answer("<b>Шаг 6 из 6 — поделитесь номером телефона</b>", reply_markup=keyboards.phone_kb(), parse_mode="HTML")
    return True


@router.callback_query(F.data == "db_tg_name", DemoBooking.name)
async def tg_name(c, state):
    if not await accept_name(c.from_user.first_name or "", c.message, state): return await c.answer("Введите имя вручную", show_alert=True)
    await c.answer()


@router.message(DemoBooking.name)
async def name(m, state):
    if not await accept_name(m.text or "", m, state): await m.answer("Пожалуйста, напишите имя до 80 символов.")


@router.message(DemoBooking.phone)
async def phone(m, state):
    if not m.contact:
        return await m.answer(
            "Нажмите кнопку «Поделиться номером», чтобы продолжить.",
            reply_markup=keyboards.phone_kb(),
        )
    if m.contact.user_id and m.contact.user_id != m.from_user.id:
        return await m.answer(
            "Пожалуйста, поделитесь своим номером кнопкой ниже.",
            reply_markup=keyboards.phone_kb(),
        )
    value = normalize_contact_phone(m.contact.phone_number)
    if not value:
        return await m.answer(
            "Не удалось распознать номер. Нажмите «Поделиться номером» ещё раз.",
            reply_markup=keyboards.phone_kb(),
        )
    await state.update_data(phone=value); data = await state.get_data()
    await m.answer("Номер получен.", reply_markup=ReplyKeyboardRemove())
    if data["service"] in COLORING_SERVICES:
        await state.set_state(DemoBooking.hair_length); return await m.answer("Какая длина волос?")
    await confirmation(m, state)


async def text_step(m, state, key, next_state, prompt):
    value = (m.text or "").strip()
    if not value or len(value) > MAX_TEXT_FIELD: return await m.answer(f"Ответьте короче (максимум {MAX_TEXT_FIELD} символов).")
    await state.update_data(**{key: value}); await state.set_state(next_state); await m.answer(prompt)


@router.message(DemoBooking.hair_length)
async def hair_length(m, state): await text_step(m, state, "hair_length", DemoBooking.result, "Какой результат хотите получить?")
@router.message(DemoBooking.result)
async def result(m, state): await text_step(m, state, "desired_result", DemoBooking.last_coloring, "Когда было последнее окрашивание?")
@router.message(DemoBooking.last_coloring)
async def last_coloring(m, state):
    value = (m.text or "").strip()
    if not value or len(value) > MAX_TEXT_FIELD: return await m.answer(f"Ответьте короче (максимум {MAX_TEXT_FIELD} символов).")
    await state.update_data(last_coloring=value); await state.set_state(DemoBooking.photo)
    await m.answer("Готовы прислать фото волос при дневном свете?", reply_markup=_kb(["Да", "Нет"], "db_photo"))


@router.callback_query(F.data.startswith("db_photo:"), DemoBooking.photo)
async def photo(c, state):
    try: value = ["да", "нет"][int(c.data.rsplit(":", 1)[1])]
    except (ValueError, IndexError): return await c.answer("Выберите ответ", show_alert=True)
    await state.update_data(photo_ready=value); await confirmation(c.message, state); await c.answer()


async def confirmation(message, state):
    data = await state.get_data(); parts = []
    for label, key in (("Длина", "hair_length"), ("Результат", "desired_result"), ("Последнее окрашивание", "last_coloring"), ("Фото", "photo_ready")):
        if data.get(key): parts.append(f"{label}: {data[key]}")
    await state.update_data(additional="; ".join(parts)); await state.set_state(DemoBooking.confirm)
    text = "<b>Проверьте данные</b>\n" + "\n".join(f"{label}: {html.escape(str(data.get(key) or '—'))}" for label, key in (("Филиал", "branch"), ("Мастер", "master"), ("Услуга", "service"), ("Дата", "date"), ("Время", "time"), ("Имя", "name"), ("Телефон", "phone")))
    await message.answer(text, parse_mode="HTML", reply_markup=_kb(["Да, отправить"], "db_confirm", "db_back:details"))


async def recover_request(c, state):
    data = await state.get_data()
    if not data.get("booking_id"):
        return await c.answer("Запись для восстановления не найдена.", show_alert=True)
    try:
        await finish(c.message, state, "Запись", "booking")
    except Exception:
        await c.message.edit_text(
            "Запись уже создана, но заявку администратору сохранить не удалось. "
            "Нажмите «Восстановить заявку», чтобы повторить.",
            reply_markup=booking_recovery_kb(),
        )
        return await c.answer("Заявка пока не восстановлена.", show_alert=True)
    await c.answer("Запись уже создана; заявка сохранена.", show_alert=True)


@router.callback_query(F.data == "db_recover_request", DemoBooking.confirm)
async def recover_request_callback(c, state):
    await recover_request(c, state)


@router.callback_query(F.data == "db_confirm:0", DemoBooking.confirm)
async def confirm(c, state):
    data = await state.get_data(); key = resource(data)
    if data.get("confirmed") and data.get("booking_id"):
        return await recover_request(c, state)
    booking = {"date": data["date"], "time": data["time"], "name": data["name"], "telegram_id": c.from_user.id, "username": c.from_user.username or "", "master": key, "master_key": key, "service": data["service"], "price": config.SERVICES.get(data["service"], 0), "duration_minutes": data.get("duration_minutes"), "comment": data.get("additional", "")}
    booking_id = await storage.save_booking(
        booking, owner_id=c.from_user.id, owner_token=data.get("lock_token"), require_live_lock=True,
    )
    if not booking_id:
        await state.update_data(time=None, lock_token=None); await state.set_state(DemoBooking.time)
        await c.message.edit_text("Бронь времени истекла. Выберите свободное время заново.", reply_markup=demo_time_slots_kb(await booking_engine._get_available_slots(data["date"], key, data.get("duration_minutes"))))
        return await c.answer("Бронь времени истекла.", show_alert=True)
    await state.update_data(booking_id=booking_id, confirmed=True)
    try:
        await finish(c.message, state, "Запись", "booking")
    except Exception:
        await state.update_data(booking_id=booking_id, confirmed=True)
        await c.message.edit_text(
            "Запись уже создана, но заявку администратору сохранить не удалось. "
            "Нажмите «Восстановить заявку», чтобы повторить.",
            reply_markup=booking_recovery_kb(),
        )
        return await c.answer("Запись создана; заявка требует восстановления.", show_alert=True)
    try: await c.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await c.answer()


@router.callback_query(F.data.startswith("db_back:"), DemoBooking.branch, DemoBooking.service, DemoBooking.date, DemoBooking.time, DemoBooking.name, DemoBooking.phone, DemoBooking.hair_length, DemoBooking.result, DemoBooking.last_coloring, DemoBooking.photo, DemoBooking.confirm)
async def back(c, state):
    target = c.data.rsplit(":", 1)[1]; data = await state.get_data()
    if target not in {"service", "date", "time", "details"}: return await c.answer("Сессия устарела. Начните запись заново.", show_alert=True)
    if target != "details": await release(c.from_user.id, state)
    if target == "service": await state.set_state(DemoBooking.service); await c.message.edit_text("<b>Шаг 2 из 6 — выберите услугу</b>", reply_markup=_kb(SERVICES, "db_service", "demo_book"), parse_mode="HTML")
    elif target == "date":
        dates = await booking_engine._get_next_dates(); await state.update_data(eligible_dates=list(dates)); await state.set_state(DemoBooking.date); await c.message.edit_text("<b>Шаг 3 из 6 — выберите дату</b>", reply_markup=keyboards.dates_kb(dates, back="db_back:service"), parse_mode="HTML")
    elif target == "details":
        await state.set_state(DemoBooking.phone)
        await c.message.answer("Поделитесь номером ещё раз. Остальные данные сохранены.", reply_markup=keyboards.phone_kb())
    else: await state.set_state(DemoBooking.time); await c.message.edit_text("<b>Шаг 4 из 6 — выберите время</b>", reply_markup=demo_time_slots_kb(await booking_engine._get_available_slots(data["date"], resource(data), data.get("duration_minutes"))), parse_mode="HTML")
    await c.answer()


@router.callback_query(F.data == "no_slots", DemoBooking.time)
async def no_slots(c, state):
    await c.answer("На эту дату свободного времени нет. Выберите другую дату.", show_alert=True)


@router.callback_query(F.data == "demo_booking_cancel")
async def cancel(c, state):
    await release(c.from_user.id, state); await state.clear(); await c.message.edit_text("Заявка отменена. Нажмите /start, чтобы вернуться в меню."); await c.answer()
