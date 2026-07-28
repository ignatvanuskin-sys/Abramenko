import re
import html
import logging
import uuid
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import messages
import keyboards
import config
import storage
import scheduler
from tz_utils import get_now
from emoji_config import E, P
from handlers.start import ContactStates
from utils import edit_with_retry, notify_admins

logger = logging.getLogger(__name__)

router = Router()


_REQUIRED = {
    "cb_choose_date": (),
    "cb_choose_time": ("date",),
    "cb_add_to_waitlist": ("date",),
    "handle_enter_name": ("date", "time", "service", "price"),
    "cb_use_tg_name": ("date", "time", "service", "price"),
}


async def _fsm_guard(callback_or_msg, state: FSMContext, *required_keys: str) -> dict | None:
    """Return FSM data dict if all required_keys present, else answer user and return None."""
    data = await state.get_data()
    missing = [k for k in required_keys if k not in data or data[k] is None]
    if not missing:
        return data
    logger.warning("FSM guard: missing keys %s, data=%s", missing, list(data.keys()))
    await state.clear()
    text = f"{E.RELOAD} Сессия устарела. Начните запись заново."
    from aiogram.types import CallbackQuery as _CQ, Message as _Msg
    if isinstance(callback_or_msg, _CQ):
        await callback_or_msg.answer(text, show_alert=True)
        try:
            await callback_or_msg.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback_or_msg.message.answer(text, reply_markup=keyboards.main_menu_kb(), parse_mode="HTML")
    else:
        await callback_or_msg.answer(text, reply_markup=keyboards.main_menu_kb(), parse_mode="HTML")
    return None


async def _finalize_booking(booking: dict, booking_id: str, send_fn, bot, discount_info: str = "") -> None:
    """Send confirmation, notify admin, schedule reminders."""
    date_str = keyboards._format_date(booking["date"])
    price_fmt = "{:,}".format(booking["price"]).replace(",", " ")
    duration = storage.normalize_duration_minutes(
        booking.get("duration_minutes") or config.get_service_duration(booking.get("service", ""))
    )
    text = messages.BOOKING_CONFIRMED.format(
        date=date_str,
        time=booking["time"],
        service=html.escape(booking["service"]),
        price=price_fmt,
        address=html.escape(config.SALON_ADDRESS),
    )
    text = text.replace(
        f"{E.BARBER} {html.escape(booking['service'])} — {price_fmt} ₸",
        f"{E.BARBER} {html.escape(booking['service'])} — {price_fmt} ₸\n{E.CLOCK} Длительность: {_format_duration(duration)}",
    )
    if discount_info:
        text += "\n\n" + html.escape(discount_info)
    await send_fn(text, keyboards.booking_success_kb(booking_id), "HTML")

    admin_text = messages.ADMIN_BOOKING_NOTIFY.format(
        name=html.escape(booking["name"]),
        service=html.escape(booking["service"]),
        date=keyboards._format_date(booking["date"]),
        time=booking["time"],
        price=booking["price"],
    )
    admin_text += f"\n{E.CLOCK} Длительность: {_format_duration(duration)}"
    await notify_admins(bot, admin_text)

    bwi = booking.copy()
    bwi["id"] = booking_id
    await scheduler.schedule_reminders(bot, bwi)


async def _safe_edit(msg, text, reply_markup=None, parse_mode="HTML"):
    """edit silently if MessageNotModified, else fallback to answer."""
    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as _e:
        if "message is not modified" not in str(_e).lower():
            try:
                await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                pass


async def _apply_discounts(telegram_id: int, base_price: int) -> tuple[int, str, int]:
    """Apply loyalty discount and bonus spending (delegates to storage for atomicity).
    NOTE: This is kept as a pre-view helper. Actual deduction happens inside
    storage.save_booking() via _apply_discounts_in_transaction.
    """
    final_price = base_price
    info_parts = []
    try:
        loyalty = await storage.get_loyalty(telegram_id)
        if loyalty:
            visits = loyalty.get("visits", 0) or 0
            if visits > 0 and visits % config.LOYALTY_VISIT_INTERVAL == 0:
                discount_amount = int(base_price * config.LOYALTY_DISCOUNT_PERCENT / 100)
                final_price -= discount_amount
                info_parts.append(
                    f"⭐ Скидка лояльности {config.LOYALTY_DISCOUNT_PERCENT}% — −{discount_amount:,} ₸".replace(",", " ")
                )
            bonuses = loyalty.get("bonuses", 0) or 0
            if bonuses > 0:
                max_bonus_spend = max(0, final_price // 2)
                bonus_spend = min(bonuses, max_bonus_spend)
                if bonus_spend > 0:
                    final_price -= bonus_spend
                    info_parts.append(
                        f"🎁 Бонусы списаны — −{bonus_spend:,} ₸ (осталось: {bonuses - bonus_spend})".replace(",", " ")
                    )
                    return max(0, final_price), "".join(info_parts), bonus_spend
    except Exception as e:
        logger.warning(f"_apply_discounts failed for {telegram_id}: {e}")
    return max(0, final_price), "".join(info_parts), 0


def _format_duration(duration_minutes: int | None) -> str:
    duration = storage.normalize_duration_minutes(duration_minutes)
    if duration % 60 == 0:
        hours = duration // 60
        return f"{hours} ч" if hours == 1 else f"{hours} ч"
    if duration > 60:
        return f"{duration // 60} ч {duration % 60} мин"
    return f"{duration} мин"


def _slot_range_fits_time_slots(start_time: str, duration_minutes: int | None, time_slots: list[str]) -> bool:
    required_slots = storage.slot_times_for_range(start_time, duration_minutes)
    available = set(time_slots)
    return all(slot in available for slot in required_slots)


class BookingStates(StatesGroup):
    choose_service = State()
    choose_date = State()
    choose_time = State()
    enter_name = State()
    enter_review_comment = State()


async def _get_next_dates(count: int = 14) -> list[str]:
    today = get_now(config.TIMEZONE).date()
    work_days = [1, 2, 3, 4, 5, 6]

    dates = []
    checked = 0
    i = 0
    while len(dates) < count and checked < 60:
        d = today + timedelta(days=i)
        if d.isoweekday() in work_days:
            dates.append(d.strftime("%Y-%m-%d"))
        i += 1
        checked += 1
    return dates


def _generate_time_slots(date_str: str) -> list[str]:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return config.TIME_SLOTS
    weekday = d.weekday()
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    day_name = day_names[weekday]
    hours = config.WORKING_HOURS.get(day_name, (10, 21))
    start_h, end_h = hours
    slots = []
    for h in range(start_h, end_h):
        slots.append(f"{h:02d}:00")
        slots.append(f"{h:02d}:30")
    return slots


async def _get_available_slots(date_str: str, master: str = None, duration_minutes: int | None = None) -> dict[str, str]:
    master = master or config.MASTER_NAME
    duration = storage.normalize_duration_minutes(duration_minutes)
    slots = {}
    booked_slots = []
    locked = set()
    unavailable_periods = []
    if date_str:
        try:
            booked_slots = await storage.get_booked_slots(date_str, master)
        except Exception as e:
            logger.error(f"Failed to get booked slots: {e}")
        try:
            locked = await storage.get_locked_slots(date_str, master)
        except Exception as e:
            logger.warning(f"Failed to check slot_locks: {e}")
        try:
            unavailable_periods = await storage.get_unavailable_periods_for_date(date_str, master)
        except Exception as e:
            logger.warning(f"Failed to check unavailable periods: {e}")
    time_slots = _generate_time_slots(date_str)

    now = get_now(config.TIMEZONE)
    today_str = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    for t in time_slots:
        requested_slices = storage.slot_times_for_range(t, duration)
        if not _slot_range_fits_time_slots(t, duration, time_slots):
            continue
        elif any(slice_time in locked for slice_time in requested_slices):
            slots[t] = "busy"
        elif any(storage.time_ranges_overlap(t, duration, b["time"], b.get("duration_minutes")) for b in booked_slots):
            slots[t] = "busy"
        elif any(storage.time_ranges_overlap(t, duration, p["start_time"], storage._duration_between(p["start_time"], p["end_time"])) for p in unavailable_periods):
            slots[t] = "busy"
        elif date_str == today_str:
            try:
                h, m = int(t[:2]), int(t[3:])
                from datetime import timedelta
                slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                cutoff = now + timedelta(minutes=config.MIN_BOOKING_ADVANCE_MINUTES)
                if slot_dt > cutoff:
                    slots[t] = "free"
            except Exception:
                slots[t] = "free"
        else:
            slots[t] = "free"
    return slots


@router.callback_query(F.data == "book")
async def cb_book(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id

    if await storage.is_user_blocked(telegram_id):
        text = (
            f"{E.LOCK} <b>Запись недоступна</b>\n\n"
            "Свяжитесь с администратором, чтобы уточнить детали."
        )
        try:
            await callback.message.edit_text(text, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await callback.answer()
        return

    user = await storage.get_user(telegram_id)
    if not user or not user.get("phone"):
        text = "<b>Для записи укажите номер телефона</b>\n\n"
        text += "Введите ваш номер телефона (например: +7 700 123 45 67)\n"
        text += f"или нажмите кнопку «{E.MOBILE} Поделиться номером»"
        try:
            await callback.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Поделиться номером", callback_data="share_phone")],
                    [InlineKeyboardButton(text="Назад", callback_data="main_menu")]
                ]),
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Поделиться номером", callback_data="share_phone")],
                    [InlineKeyboardButton(text="Назад", callback_data="main_menu")]
                ]),
                parse_mode="HTML"
            )
        await state.set_state(ContactStates.waiting_contact)
        await callback.answer()
        return

    if await storage.has_active_booking(telegram_id):
        try:
            await callback.message.edit_text(
                messages.max_bookings_reached_text(),
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                messages.max_bookings_reached_text(),
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML"
            )
        await callback.answer()
        return

    if not await storage.user_rate_limit_check(
        telegram_id, window=config.RATE_LIMIT_WINDOW, max_attempts=config.MAX_BOOKING_ATTEMPTS
    ):
        try:
            await callback.message.edit_text(
                messages.RATE_LIMIT,
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                messages.RATE_LIMIT,
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML",
            )
        await callback.answer()
        return

    await state.update_data(master=config.MASTER_NAME)
    await state.set_state(BookingStates.choose_service)
    await _safe_edit(
        callback.message,
        messages.CHOOSE_SERVICE,
        reply_markup=await keyboards.services_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("service:"), BookingStates.choose_service)
async def cb_choose_service(callback: CallbackQuery, state: FSMContext):
    service_key = callback.data.split(":", 1)[1]
    service_name = None
    price = None

    if service_key.isdigit():
        idx = int(service_key)
        service_items = list(config.SERVICES.items())
        if 0 <= idx < len(service_items):
            service_name, price = service_items[idx]
    else:
        service_name = service_key
        price = config.SERVICES.get(service_name)

    if not service_name or service_name not in config.SERVICES:
        await callback.answer(f"{P.CROSS} Услуга не найдена", show_alert=True)
        return

    price = config.SERVICES.get(service_name, 0)
    duration = config.get_service_duration(service_name)
    await state.update_data(service=service_name, price=price, duration_minutes=duration)
    await state.set_state(BookingStates.choose_date)
    dates = await _get_next_dates()

    text = messages.service_selected(service_name, price, duration)

    await _safe_edit(
        callback.message,
        text,
        reply_markup=keyboards.dates_kb(dates),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("date:"))
async def cb_choose_date(callback: CallbackQuery, state: FSMContext):
    _state = await state.get_state()
    if _state != BookingStates.choose_date.state:
        await callback.answer(
            "Сессия записи устарела. Нажмите «Записаться» снова.",
            show_alert=True
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    date_str = callback.data.split(":", 1)[1]
    try:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await callback.answer(f"{P.CROSS} Неверная дата", show_alert=True)
        return

    today = get_now(config.TIMEZONE).date()
    if selected_date < today:
        await callback.answer(f"{P.CROSS} Нельзя выбрать прошедшую дату", show_alert=True)
        return

    MAX_DAYS_AHEAD = 60
    if selected_date > today + timedelta(days=MAX_DAYS_AHEAD):
        await callback.answer(f"{P.CROSS} Нельзя записаться более чем на {MAX_DAYS_AHEAD} дней вперёд", show_alert=True)
        return

    await state.update_data(date=date_str)
    await state.set_state(BookingStates.choose_time)

    try:
        data = await state.get_data()
        slots = await _get_available_slots(date_str, duration_minutes=data.get("duration_minutes"))
    except Exception as e:
        logger.error(f"Failed to get available slots: {e}")
        slots = {t: "free" for t in _generate_time_slots(date_str)}

    free_count = sum(1 for status in slots.values() if status == "free")
    busy_count = sum(1 for status in slots.values() if status == "busy")

    if not any(v == "free" for v in slots.values()):
        await callback.answer(
            "На этот день свободных слотов нет. Выберите другой день.",
            show_alert=True
        )
        return

    date_formatted = keyboards._format_date(date_str)
    text = messages.date_selected(date_formatted)
    text = text.replace("Выберите свободный слот:", f"Свободно: {free_count} | Занято: {busy_count}\n\nВыберите свободный слот:")

    await _safe_edit(
        callback.message,
        text,
        reply_markup=keyboards.time_slots_kb(slots),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("time:"), BookingStates.choose_time)
async def cb_choose_time(callback: CallbackQuery, state: FSMContext):
    time_str = callback.data.split(":", 1)[1]
    data = await state.get_data()
    date_str = data.get("date", "")

    try:
        available_slots = await _get_available_slots(date_str, duration_minutes=data.get("duration_minutes"))
        slot_status = available_slots.get(time_str)

        if slot_status is None:
            await callback.answer(
                f"Слот недоступен. Минимальное время до записи: {config.MIN_BOOKING_ADVANCE_MINUTES} мин.",
                show_alert=True
            )
            return
        elif slot_status != "free":
            await callback.answer(
                f"{E.CROSS} Этот слот уже занят! Пожалуйста, выберите другое время.",
                show_alert=True
            )
            slots = await _get_available_slots(date_str, duration_minutes=data.get("duration_minutes"))
            free_count = sum(1 for status in slots.values() if status == "free")
            busy_count = sum(1 for status in slots.values() if status == "busy")

            date_formatted = keyboards._format_date(date_str)
            text = messages.date_selected(date_formatted)
            text = text.replace("Выберите свободный слот:", f"Свободно: {free_count} | Занято: {busy_count}\n\nВыберите свободный слот:")

            try:
                await callback.message.edit_text(
                    text,
                    reply_markup=keyboards.time_slots_kb(slots),
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return
    except Exception as e:
        logger.error(f"Failed to verify slot availability: {e}")

    available_slots = _generate_time_slots(data.get("date", ""))
    if time_str not in available_slots:
        await callback.answer(f"{P.CROSS} Неверное время", show_alert=True)
        return

    lock_owner_token = uuid.uuid4().hex
    lock_acquired = await storage.create_slot_lock(
        date_str,
        time_str,
        config.MASTER_NAME,
        duration_minutes=data.get("duration_minutes"),
        owner_id=callback.from_user.id,
        owner_token=lock_owner_token,
    )
    if not lock_acquired:
        await callback.answer(f"{P.CROSS} Этот слот только что заняли. Выберите другое время.", show_alert=True)
        slots = await _get_available_slots(date_str, duration_minutes=data.get("duration_minutes"))
        free_count = sum(1 for status in slots.values() if status == "free")
        busy_count = sum(1 for status in slots.values() if status == "busy")
        date_formatted = keyboards._format_date(date_str)
        text = messages.date_selected(date_formatted)
        text = text.replace(
            "Выберите свободный слот:",
            f"Свободно: {free_count} | Занято: {busy_count}\n\nВыберите свободный слот:",
        )
        await _safe_edit(callback.message, text, reply_markup=keyboards.time_slots_kb(slots), parse_mode="HTML")
        return

    await state.update_data(time=time_str, lock_owner_token=lock_owner_token)
    await state.set_state(BookingStates.enter_name)

    data_so_far = await state.get_data()
    text = f"{E.LIST} <b>Шаг 4 из 4 — Ваше имя</b>\n\n"
    text += f"Вы записываетесь:\n"
    text += f"{E.SCISSORS} {html.escape(data_so_far.get('service',''))}\n"
    text += f"{E.CALENDAR} {keyboards._format_date(data_so_far.get('date',''))} в {time_str}\n\n"
    text += f"{E.CLOCK} Длительность: {_format_duration(data_so_far.get('duration_minutes'))}\n\n"
    text += f"{E.USER} <b>Как к вам обращаться?</b>\n"
    text += "Введите имя текстом ответным сообщением. Только буквы, без цифр и спецсимволов."

    tg_name = callback.from_user.first_name or ""
    await state.update_data(tg_name_suggestion=tg_name)
    name_rows = []
    if tg_name and re.match(r'^[a-zA-Zа-яА-ЯёЁ\s\-\']+$', tg_name) and re.search(r'[a-zA-Zа-яА-ЯёЁ]', tg_name) and len(tg_name) <= 50:
        name_rows.append([InlineKeyboardButton(text=f"Использовать «{tg_name}»", callback_data="use_tg_name")])
    name_rows.append([InlineKeyboardButton(text="Назад к времени", callback_data="back_to_time")])
    name_rows.append([InlineKeyboardButton(text="Отменить", callback_data="cancel_booking")])
    back_kb = InlineKeyboardMarkup(inline_keyboard=name_rows)

    await _safe_edit(
        callback.message,
        text,
        reply_markup=back_kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "use_tg_name", BookingStates.enter_name)
async def cb_use_tg_name(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    name = data.get("tg_name_suggestion", "")
    if not name:
        await callback.answer(f"{P.CROSS} Имя не найдено, введите вручную", show_alert=True)
        return

    telegram_id = callback.from_user.id
    await state.update_data(name=name)
    data = await _fsm_guard(callback, state, "date", "time", "service", "price")
    if data is None:
        return

    booking = {
        "date": data["date"],
        "time": data["time"],
        "name": name,
        "telegram_id": telegram_id,
        "username": callback.from_user.username or "",
        "master": config.MASTER_NAME,
        "service": data["service"],
        "price": data["price"],
        "duration_minutes": data.get("duration_minutes") or config.get_service_duration(data["service"]),
        "apply_discounts": True,
    }

    try:
        booking_id = await storage.save_booking(booking)
    except Exception as e:
        logger.error(f"Failed to save booking: {e}")
        await callback.message.answer(messages.ERROR, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await state.clear()
        await callback.answer()
        return

    if not booking_id:
        _d2 = await state.get_data()
        await storage.release_slot_lock(
            _d2.get("date", ""),
            _d2.get("time", ""),
            config.MASTER_NAME,
            duration_minutes=_d2.get("duration_minutes"),
            owner_id=telegram_id,
            owner_token=_d2.get("lock_owner_token"),
        )
        await callback.message.answer(messages.SLOT_BUSY, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await state.set_state(BookingStates.choose_time)
        await callback.answer()
        return

    bot = callback.bot
    await _finalize_booking(
        booking, booking_id,
        lambda text, kb, pm: callback.message.answer(text, reply_markup=kb, parse_mode=pm),
        bot,
        discount_info=booking.get("discount_info", ""),
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data.startswith("waitlist:"), BookingStates.choose_time)
async def cb_waitlist(callback: CallbackQuery, state: FSMContext):
    time_str = callback.data.split(":", 1)[1]
    telegram_id = callback.from_user.id

    data = await _fsm_guard(callback, state, "date", "service")
    if data is None:
        return
    duration = data.get("duration_minutes") or config.get_service_duration(data["service"])
    try:
        available_slots = await _get_available_slots(data["date"], duration_minutes=duration)
        if available_slots.get(time_str) != "busy":
            await callback.answer(
                "Этот слот больше недоступен для листа ожидания. Обновите выбор времени.",
                show_alert=True,
            )
            return
    except Exception as e:
        logger.error(f"Failed to revalidate waitlist slot: {e}")
        await callback.answer("Не удалось проверить слот. Попробуйте снова.", show_alert=True)
        return

    existing_waitlist = await storage.get_waitlist_for_slot(data["date"], time_str, config.MASTER_NAME)
    if any(wl["telegram_id"] == telegram_id and wl["status"] == "waiting" for wl in existing_waitlist):
        try:
            await callback.message.edit_text(
                f"{E.WARNING} Вы уже в листе ожидания на это время.\n\n"
                f"Мы уведомим вас, если слот освободится.",
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                f"{E.WARNING} Вы уже в листе ожидания на это время.\n\n"
                f"Мы уведомим вас, если слот освободится.",
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML"
            )
        await callback.answer()
        return

    _wl_count = await storage.get_user_waitlist_count(telegram_id)
    if _wl_count >= 5:
        await callback.answer("Максимум 5 записей в листе ожидания.", show_alert=True)
        return
    added = await storage.add_to_waitlist(
        telegram_id=telegram_id,
        name=callback.from_user.first_name or "",
        master=config.MASTER_NAME,
        service=data["service"],
        date=data["date"],
        time=time_str,
        duration_minutes=duration,
    )
    if not added:
        await callback.answer(
            "Не удалось добавить в лист ожидания: слот устарел или запись уже существует.",
            show_alert=True,
        )
        return
    await _safe_edit(
        callback.message,
        messages.WAITLIST_ADDED.format(date=keyboards._format_date(data["date"]), time=time_str),
        reply_markup=keyboards.back_to_main_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BookingStates.enter_name)
async def handle_enter_name(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer(f"{E.CROSS} Пожалуйста, введите имя текстом.", parse_mode="HTML")
        return
    name = message.text.strip()

    if not name or len(name) > 50:
        await message.answer(f"{E.CROSS} Имя должно содержать от 1 до 50 символов.", parse_mode="HTML")
        return

    if not re.match(r'^[a-zA-Zа-яА-ЯёЁ\s\-\']+$', name):
        await message.answer(f"{E.CROSS} Имя может содержать только буквы, пробелы, дефисы и апострофы.", parse_mode="HTML")
        return

    if not re.search(r'[a-zA-Zа-яА-ЯёЁ]', name):
        await message.answer(f"{E.CROSS} Имя должно содержать хотя бы одну букву.", parse_mode="HTML")
        return

    await state.update_data(name=name)
    telegram_id = message.from_user.id

    data = await _fsm_guard(message, state, "date", "time", "service", "price")
    if data is None:
        return
    booking = {
        "date": data["date"],
        "time": data["time"],
        "name": name,
        "telegram_id": telegram_id,
        "username": message.from_user.username or "",
        "master": config.MASTER_NAME,
        "service": data["service"],
        "price": data["price"],
        "duration_minutes": data.get("duration_minutes") or config.get_service_duration(data["service"]),
        "apply_discounts": True,
    }

    try:
        booking_id = await storage.save_booking(booking)
    except Exception as e:
        logger.error(f"Failed to save booking: {e}")
        await message.answer(messages.ERROR, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await state.clear()
        return

    if not booking_id:
        _d = await state.get_data()
        await storage.release_slot_lock(
            _d.get("date", ""),
            _d.get("time", ""),
            config.MASTER_NAME,
            duration_minutes=_d.get("duration_minutes"),
            owner_id=telegram_id,
            owner_token=_d.get("lock_owner_token"),
        )
        await message.answer(messages.SLOT_BUSY, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await state.set_state(BookingStates.choose_time)
        return

    bot = message.bot
    await _finalize_booking(
        booking, booking_id,
        lambda text, kb, pm: message.answer(text, reply_markup=kb, parse_mode=pm),
        bot,
        discount_info=booking.get("discount_info", ""),
    )
    await state.clear()


@router.callback_query(F.data == "confirm")
async def cb_confirm_deprecated(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await callback.answer(
            "Вы в процессе записи. Завершите или отмените запись.",
            show_alert=True
        )
        return
    await state.clear()
    await _safe_edit(
        callback.message,
        f"{E.INFO} Сессия устарела. Начните запись заново.",
        reply_markup=keyboards.back_to_main_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_booking")
async def cb_cancel_booking(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("date") and data.get("time") and data.get("master"):
        await storage.release_slot_lock(
            data["date"],
            data["time"],
            data.get("master", config.MASTER_NAME),
            duration_minutes=data.get("duration_minutes"),
            owner_id=callback.from_user.id,
            owner_token=data.get("lock_owner_token"),
        )
    await state.clear()
    book_again_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Записаться снова", callback_data="book")],
        [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")],
    ])
    await callback.message.edit_text(
        messages.BOOKING_CANCELLED,
        reply_markup=book_again_kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_service")
async def cb_back_to_service(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("date") and data.get("time") and data.get("master"):
        await storage.release_slot_lock(
            data["date"], data["time"], config.MASTER_NAME,
            duration_minutes=data.get("duration_minutes"),
            owner_id=callback.from_user.id,
            owner_token=data.get("lock_owner_token"),
        )
    await state.update_data(service=None, date=None, time=None, name=None, lock_owner_token=None)
    await state.set_state(BookingStates.choose_service)
    await _safe_edit(
        callback.message,
        messages.CHOOSE_SERVICE,
        reply_markup=await keyboards.services_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_date")
async def cb_back_to_date(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("date") and data.get("time") and data.get("master"):
        await storage.release_slot_lock(
            data["date"], data["time"], config.MASTER_NAME,
            duration_minutes=data.get("duration_minutes"),
            owner_id=callback.from_user.id,
            owner_token=data.get("lock_owner_token"),
        )
    await state.update_data(time=None, name=None, lock_owner_token=None)
    await state.set_state(BookingStates.choose_date)
    dates = await _get_next_dates()

    text = messages.CHOOSE_DATE
    if data.get("service"):
        text = f"{E.CHECK} {data['service']} — {data.get('price', 0):,} ₸\n\n".replace(",", " ") + text

    await _safe_edit(
        callback.message,
        text,
        reply_markup=keyboards.dates_kb(dates),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_time")
async def cb_back_to_time(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("date") and data.get("time") and data.get("master"):
        await storage.release_slot_lock(
            data["date"], data["time"], config.MASTER_NAME,
            duration_minutes=data.get("duration_minutes"),
            owner_id=callback.from_user.id,
            owner_token=data.get("lock_owner_token"),
        )
    await state.update_data(name=None, lock_owner_token=None)
    await state.set_state(BookingStates.choose_time)
    data = await state.get_data()

    slots = await _get_available_slots(data.get("date", ""), duration_minutes=data.get("duration_minutes"))
    free_count = sum(1 for status in slots.values() if status == "free")
    busy_count = sum(1 for status in slots.values() if status == "busy")

    date_formatted = keyboards._format_date(data.get('date', ''))
    text = messages.date_selected(date_formatted)
    text = text.replace("Выберите свободный слот:", f"Свободно: {free_count} | Занято: {busy_count}\n\nВыберите свободный слот:")

    await _safe_edit(
        callback.message,
        text,
        reply_markup=keyboards.time_slots_kb(slots),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "no_slots")
async def cb_no_slots(callback: CallbackQuery):
    await callback.answer(
        f"{E.EMPTY} Все слоты на эту дату заняты. Попробуйте выбрать другую дату или встаньте в лист ожидания.",
        show_alert=True,
    )


@router.callback_query(F.data == "go_to_waitlist")
async def cb_go_to_waitlist(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    date_str = data.get("date", "")

    slots = await _get_available_slots(date_str, duration_minutes=data.get("duration_minutes"))
    busy_slots = {k: v for k, v in slots.items() if v == "busy"}

    if not busy_slots:
        await callback.answer(f"{P.EMPTY} Нет занятых слотов для листа ожидания", show_alert=True)
        return

    text = f"{E.RELOAD} <b>Лист ожидания</b>\n\n"
    text += f"Выберите занятое время, на которое хотите встать в очередь:\n\n"
    text += f"{E.INFO} Мы уведомим вас, если это время освободится."

    buttons = []
    busy_times = list(busy_slots.keys())
    for i in range(0, len(busy_times), 4):
        row = []
        for time_str in busy_times[i:i+4]:
            row.append(InlineKeyboardButton(text=f"🔴 {time_str}", callback_data=f"waitlist:{time_str}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="back_to_time")])

    await _safe_edit(
        callback.message,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("remind_confirm:"))
async def cb_remind_confirm(callback: CallbackQuery, bot: Bot):
    booking_id = callback.data.split(":", 1)[1]
    try:
        booking = await storage.get_booking_with_user(booking_id)
        if booking:
            notify_text = (
                f"{E.CHECK} <b>Клиент подтвердил визит</b>\n\n"
                f"{E.USER} {html.escape(booking['name'])} — "
                f"{keyboards._format_date(booking['date'])} в {booking['time']}\n"
                f"{E.BARBER} Услуга: {html.escape(booking['service'])}"
            )
            await notify_admins(bot, notify_text)
    except Exception as e:
        logger.error(f"Failed to notify admin on remind_confirm: {e}")
    try:
        await callback.message.edit_text(
            f"✅ <b>Отлично!</b> Ждём вас в указанное время.",
            reply_markup=keyboards.back_to_main_kb(),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("remind_cancel:"))
async def cb_remind_cancel(callback: CallbackQuery, bot: Bot):
    booking_id = callback.data.split(":", 1)[1]
    booking = await storage.cancel_booking(booking_id, telegram_id=callback.from_user.id)
    if booking:
        await scheduler.cancel_reminders(booking_id)
        try:
            await callback.message.edit_text(messages.BOOKING_CANCELLED, parse_mode="HTML")
        except Exception:
            await callback.message.answer(messages.BOOKING_CANCELLED, parse_mode="HTML")
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.TIMEZONE)
            visit_dt = datetime.strptime(f"{booking['date']} {booking['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            now = datetime.now(tz)
            if visit_dt - now <= timedelta(hours=2):
                admin_text = messages.CANCEL_LAST_MINUTE_ADMIN.format(
                    name=html.escape(booking.get("name", "Неизвестно")),
                    date=html.escape(keyboards._format_date(booking["date"])),
                    time=html.escape(booking["time"]),
                    service=html.escape(booking["service"]),
                )
                await notify_admins(bot, admin_text)
        except Exception as e:
            logger.error(f"Last-minute cancel admin notify error: {e}")

        waitlist = await storage.get_waitlist_for_open_period(
            booking["date"],
            config.MASTER_NAME,
            booking["time"],
            booking.get("duration_minutes"),
        )
        for wl in waitlist:
            try:
                text = messages.WAITLIST_OFFER.format(
                    date=keyboards._format_date(booking["date"]),
                    time=booking["time"],
                )
                await bot.send_message(wl["telegram_id"], text)
                await storage.update_waitlist_status(wl["id"], "offered")
            except Exception as e:
                logger.error(f"Failed to notify waitlist {wl['telegram_id']}: {e}")
    else:
        try:
            await callback.message.edit_text("Запись не найдена или уже отменена.", parse_mode="HTML")
        except Exception:
            await callback.message.answer("Запись не найдена или уже отменена.", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("review:"))
async def cb_review(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Ошибка", show_alert=True)
        return
    booking_id = parts[1]
    try:
        rating = int(parts[2])
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    if rating < 1 or rating > 5:
        await callback.answer("Оценка должна быть от 1 до 5", show_alert=True)
        return

    await state.update_data(review_booking_id=booking_id, review_rating=rating)
    await state.set_state(BookingStates.enter_review_comment)

    stars = f"{E.STAR}" * rating
    text = f"Спасибо за оценку! {stars}\n\n"
    text += f"{E.COMMENT} Хотите оставить комментарий?\n\n"
    text += "Напишите ваш отзыв или нажмите «Пропустить»:"

    await _safe_edit(
        callback.message,
        text,
        reply_markup=keyboards.skip_comment_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BookingStates.enter_review_comment)
async def handle_review_comment(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer(f"{E.CROSS} Пожалуйста, введите комментарий текстом или нажмите «Пропустить».", parse_mode="HTML")
        return

    comment = message.text.strip()
    if len(comment) > 500:
        await message.answer(f"{E.CROSS} Комментарий слишком длинный. Максимум 500 символов.", parse_mode="HTML")
        return

    data = await state.get_data()
    booking_id = data.get("review_booking_id")
    rating = data.get("review_rating")

    if not booking_id or not rating:
        await message.answer(f"{E.CROSS} Ошибка. Попробуйте заново.", parse_mode="HTML")
        await state.clear()
        return

    saved = await storage.save_review(booking_id, message.from_user.id, rating, comment)
    if saved:
        await message.answer(
            f"{E.CHECK} Спасибо за подробный отзыв!\n\n"
            f"{E.STAR} Оценка: {rating}/5\n"
            f"{E.COMMENT} Комментарий: {html.escape(comment)}",
            reply_markup=keyboards.back_to_main_kb(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"{E.WARNING} Вы уже оставляли отзыв на эту запись.",
            reply_markup=keyboards.back_to_main_kb()
        )

    await state.clear()


@router.callback_query(F.data == "skip_comment")
async def cb_skip_comment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    booking_id = data.get("review_booking_id")
    rating = data.get("review_rating")

    if not booking_id or not rating:
        await callback.answer(f"{P.CROSS} Ошибка", show_alert=True)
        return

    saved = await storage.save_review(booking_id, callback.from_user.id, rating, "")
    if saved:
        try:
            await callback.message.edit_text(
                f"✅ Спасибо за оценку! ⭐ {rating}/5",
                reply_markup=keyboards.back_to_main_kb()
            )
        except Exception:
            await callback.message.answer(
                f"✅ Спасибо за оценку! ⭐ {rating}/5",
                reply_markup=keyboards.back_to_main_kb()
            )
    else:
        try:
            await callback.message.edit_text(
                "⚠️ Вы уже оставляли отзыв на эту запись.",
                reply_markup=keyboards.back_to_main_kb()
            )
        except Exception:
            await callback.message.answer(
                "⚠️ Вы уже оставляли отзыв на эту запись.",
                reply_markup=keyboards.back_to_main_kb()
            )

    await state.clear()
    await callback.answer()
