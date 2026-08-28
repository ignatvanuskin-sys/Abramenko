import html
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
import keyboards
import messages
import scheduler
import storage
from emoji_config import E, P
from handlers.start import ContactStates
from tz_utils import get_now
from utils import edit_with_retry, notify_admins  # noqa: F401  (kept: patched in tests)

logger = logging.getLogger(__name__)

router = Router()


# ---- FSM DATA VALIDATION HELPER ----------------------------------------
_REQUIRED = {
    "cb_choose_date": ("master",),
    "cb_choose_time": ("master", "date"),
    "cb_add_to_waitlist": ("master", "date"),
    "handle_enter_name": ("master", "date", "time", "service", "price"),
    "cb_use_tg_name": ("master", "date", "time", "service", "price"),
}


async def _fsm_guard(callback_or_msg, state: FSMContext, *required_keys: str) -> dict | None:
    """Return FSM data dict if all required_keys present, else answer user and return None.

    Usage:
        data = await _fsm_guard(callback, state, "master", "date")
        if data is None: return  # handler aborts gracefully
    """
    data = await state.get_data()
    missing = [k for k in required_keys if k not in data or data[k] is None]
    if not missing:
        return data
    # State is corrupt / stale - redirect user gracefully
    logger.warning("FSM guard: missing keys %s, data=%s", missing, list(data.keys()))
    await state.clear()
    text = f"{E.RELOAD} Сессия устарела. Начните запись заново."
    from aiogram.types import CallbackQuery as callback_query  # noqa: N813

    if isinstance(callback_or_msg, callback_query):
        await callback_or_msg.answer(text, show_alert=True)
        try:
            await callback_or_msg.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback_or_msg.message.answer(text, reply_markup=keyboards.main_menu_kb(), parse_mode="HTML")
    else:
        await callback_or_msg.answer(text, reply_markup=keyboards.main_menu_kb(), parse_mode="HTML")
    return None


# --------------------------------------------------------------------------


# BUG-S1 FIX: Shared booking finalization (DRY)
async def _finalize_booking(booking: dict, booking_id: str, send_fn, bot, discount_info: str = "") -> None:
    """Send confirmation, notify admin/master, schedule reminders.
    send_fn: async callable(text, reply_markup, parse_mode)
    """
    date_str = keyboards._format_date(booking["date"])
    price_fmt = "{:,}".format(booking["price"]).replace(",", " ")
    # BUG-C5 FIX: use BOOKING_CONFIRMED template instead of manual f-string assembly
    text = messages.BOOKING_CONFIRMED.format(
        date=date_str,
        time=booking["time"],
        master=html.escape(booking["master"]),
        service=html.escape(booking["service"]),
        price=price_fmt,
        address=html.escape(config.SALON_ADDRESS),
    )
    # CRIT-001/002 FIX: Append discount info if any discounts were applied
    if discount_info:
        text += "\n\n" + html.escape(discount_info)
    await send_fn(text, keyboards.booking_success_kb(booking_id), "HTML")

    admin_text = messages.ADMIN_BOOKING_NOTIFY.format(
        name=html.escape(booking["name"]),
        master=html.escape(booking["master"]),
        service=html.escape(booking["service"]),
        date=keyboards._format_date(booking["date"]),
        time=booking["time"],
        price=booking["price"],
    )
    await notify_admins(bot, admin_text)

    bwi = booking.copy()
    bwi["id"] = booking_id
    await scheduler.schedule_reminders(bot, bwi)


async def _safe_edit(msg, text, reply_markup=None, parse_mode="HTML"):
    """BUG-S4 FIX: edit silently if MessageNotModified, else fallback to answer."""
    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as _e:
        if "message is not modified" not in str(_e).lower():
            try:
                await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                pass


async def _apply_discounts(telegram_id: int, base_price: int) -> tuple[int, str]:
    """CRIT-001/002 FIX: Apply loyalty discount and bonus spending.
    Returns (final_price, info_text) where info_text describes applied discounts.
    """
    final_price = base_price
    info_parts = []
    try:
        loyalty = await storage.get_loyalty(telegram_id)
        if loyalty:
            visits = loyalty.get("visits", 0) or 0
            bonuses = loyalty.get("bonuses", 0) or 0

            # CRIT-001: Loyalty discount — every LOYALTY_VISIT_INTERVAL completed visits
            if visits > 0 and visits % config.LOYALTY_VISIT_INTERVAL == 0:
                discount_amount = int(base_price * config.LOYALTY_DISCOUNT_PERCENT / 100)
                final_price -= discount_amount
                info_parts.append(
                    f"{E.STAR} Скидка лояльности {config.LOYALTY_DISCOUNT_PERCENT}% — −{discount_amount:,} ₸".replace(
                        ",", " "
                    )
                )

            # CRIT-002: Bonus spending — up to 50% of final price
            if bonuses > 0:
                max_bonus_spend = max(0, final_price // 2)
                bonus_spend = min(bonuses, max_bonus_spend)
                if bonus_spend > 0:
                    final_price -= bonus_spend
                    info_parts.append(
                        f"{E.GIFT} Бонусы списаны — −{bonus_spend:,} ₸ (осталось: {bonuses - bonus_spend})".replace(",", " ")
                    )
                    # Store bonus_spend in return so caller can call spend_bonus()
                    return max(0, final_price), "".join(info_parts), bonus_spend
    except Exception as e:
        logger.warning(f"_apply_discounts failed for {telegram_id}: {e}")
    return max(0, final_price), "".join(info_parts), 0


class BookingStates(StatesGroup):
    choose_master = State()
    choose_service = State()
    choose_date = State()
    choose_time = State()
    enter_name = State()
    enter_review_comment = State()


async def _get_next_dates(master_name: str = "", count: int = 14) -> list[str]:
    today = get_now(config.TIMEZONE).date()
    work_days = []
    if master_name:
        get_work_days = getattr(storage, "get_master_work_days", None)
        work_days = await get_work_days(master_name) if get_work_days else [1, 2, 3, 4, 5, 6, 7]
    else:
        work_days = [1, 2, 3, 4, 5, 6, 7]  # ежедневно по умолчанию

    dates = []
    checked = 0
    i = 0
    while len(dates) < count and checked < 60:
        d = today + timedelta(days=i)
        # isoweekday(): пн=1 ... вс=7
        if d.isoweekday() in work_days:
            dates.append(d.strftime("%Y-%m-%d"))
        i += 1
        checked += 1
    return dates


def _generate_time_slots(date_str: str) -> list[str]:
    from datetime import datetime

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
    # FIX: range(start_h, end_h) — не включаем слот в время закрытия (10,21) → 10:00..20:30
    for h in range(start_h, end_h):
        slots.append(f"{h:02d}:00")
        slots.append(f"{h:02d}:30")
    return slots


async def _get_available_slots(
    date_str: str,
    master: str,
    duration_minutes: int | None = None,
) -> dict[str, str]:
    slots: dict[str, str] = {}
    time_slots = _generate_time_slots(date_str)
    step = config.SLOT_STEP_MINUTES
    requested_duration = storage.normalize_duration_minutes(duration_minutes)
    occupied: set[str] = set()

    if date_str:
        try:
            booked_slots = await storage.get_booked_slots(date_str, master)
            for booking in booked_slots:
                start = storage.time_to_minutes(booking["time"])
                duration = storage.normalize_duration_minutes(booking.get("duration_minutes"))
                occupied.update(
                    storage.minutes_to_time(start + offset)
                    for offset in range(0, duration, step)
                )
        except Exception as e:
            logger.error("Failed to get booked slots: %s", e)
        try:
            occupied.update(await storage.get_locked_slots(date_str, master))
        except Exception as e:
            logger.warning("Failed to check slot_locks: %s", e)
        try:
            unavailable = await storage.get_unavailable_periods_for_date(date_str, master)
            for period in unavailable:
                start = storage.time_to_minutes(period["start_time"])
                end = storage.time_to_minutes(period["end_time"])
                occupied.update(
                    storage.minutes_to_time(offset)
                    for offset in range(start, end, step)
                )
        except Exception as e:
            logger.warning("Failed to check unavailable periods: %s", e)

    now = get_now(config.TIMEZONE)
    today_str = now.strftime("%Y-%m-%d")
    for time_str in time_slots:
        start = storage.time_to_minutes(time_str)
        required = [storage.minutes_to_time(start + offset) for offset in range(0, requested_duration, step)]
        if any(slice_time in occupied for slice_time in required):
            slots[time_str] = "busy"
        elif date_str == today_str:
            slot_dt = now.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
            if slot_dt > now + timedelta(minutes=config.MIN_BOOKING_ADVANCE_MINUTES):
                slots[time_str] = "free"
        else:
            slots[time_str] = "free"
    return slots


@router.callback_query(F.data == "book")
async def cb_book(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id

    # Для записи нужен контакт: сразу показываем системную Telegram-кнопку
    # запроса номера, без ручного ввода и лишнего промежуточного экрана.
    user = await storage.get_user(telegram_id)
    if not user or not user.get("phone"):
        await state.set_state(ContactStates.waiting_contact)
        await callback.message.answer(
            "<b>Чтобы начать запись, поделитесь номером телефона</b>",
            reply_markup=keyboards.phone_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if await storage.has_active_booking(telegram_id):
        try:
            await callback.message.edit_text(
                messages.max_bookings_reached_text(), reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                messages.max_bookings_reached_text(), reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML"
            )
        await callback.answer()
        return

    # HIGH-002 FIX: DB-based rate limit (persists across restarts)
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

    await state.set_state(BookingStates.choose_master)
    await _safe_edit(callback.message, messages.CHOOSE_MASTER, reply_markup=keyboards.masters_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("master:"), BookingStates.choose_master)
async def cb_choose_master(callback: CallbackQuery, state: FSMContext):
    master_key = callback.data.split(":", 1)[1]

    # Handle both index-based and name-based callbacks
    master_name = None
    if master_key.isdigit():
        # Index-based lookup
        idx = int(master_key)
        if isinstance(config.MASTERS, dict):
            master_list = list(config.MASTERS.keys())
        else:
            master_list = [item[0] for item in config.MASTERS]
        if 0 <= idx < len(master_list):
            master_name = master_list[idx]
    else:
        master_name = master_key

    if isinstance(config.MASTERS, dict):
        master_info = config.MASTERS.get(master_name)
        experience = master_info.get("experience", "") if master_info else ""
        specialization = master_info.get("specialization", "") if master_info else ""
    else:
        master_info = next((item for item in config.MASTERS if item[0] == master_name), None)
        experience = "Информация уточняется"
        specialization = master_info[1] if master_info else ""

    if not master_name or not master_info:
        await callback.answer(f"{P.CROSS} Мастер не найден", show_alert=True)
        return

    await state.update_data(master=master_name)
    await state.set_state(BookingStates.choose_service)

    text = messages.master_selected(master_name, experience, specialization)

    await _safe_edit(
        callback.message, text, reply_markup=await keyboards.services_kb(master_name=master_name), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("service:"), BookingStates.choose_service)
async def cb_choose_service(callback: CallbackQuery, state: FSMContext):
    service_key = callback.data.split(":", 1)[1]

    # Handle both index-based and name-based callbacks
    service_name = None
    if service_key.isdigit():
        # Index-based lookup
        idx = int(service_key)
        service_items = list(config.SERVICES.items())
        if 0 <= idx < len(service_items):
            service_name, price = service_items[idx]
    else:
        # Name-based lookup
        service_name = service_key
        price = config.SERVICES.get(service_name)

    if not service_name or service_name not in config.SERVICES:
        await callback.answer(f"{P.CROSS} Услуга не найдена", show_alert=True)
        return

    # FIX: use per-master price if set, otherwise global price
    _data_master = await state.get_data()
    _master_for_price = _data_master.get("master", "")
    if _master_for_price:
        price = await storage.get_effective_price(_master_for_price, service_name)
    else:
        price = config.SERVICES.get(service_name, 0)
    await state.update_data(service=service_name, price=price)
    await state.set_state(BookingStates.choose_date)
    data = await state.get_data()
    dates = await _get_next_dates(master_name=data.get("master", ""))

    text = messages.service_selected(service_name, price)

    await _safe_edit(callback.message, text, reply_markup=keyboards.dates_kb(dates), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("date:"))
async def cb_choose_date(callback: CallbackQuery, state: FSMContext):
    # FALLBACK: если состояние не choose_date — сессия устарела
    _state = await state.get_state()
    if _state != BookingStates.choose_date.state:
        await callback.answer("Сессия записи устарела. Нажмите «Записать» снова.", show_alert=True)
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

    # Validate date is not in the past (timezone-aware)
    today = get_now(config.TIMEZONE).date()
    if selected_date < today:
        await callback.answer(f"{P.CROSS} Нельзя выбрать прошедшую дату", show_alert=True)
        return

    # MED-03 FIX: Reject dates beyond allowed booking horizon (60 days)
    MAX_DAYS_AHEAD = 60  # noqa: N806
    from datetime import timedelta as _td

    if selected_date > today + _td(days=MAX_DAYS_AHEAD):
        await callback.answer(f"{P.CROSS} Нельзя записаться более чем на {MAX_DAYS_AHEAD} дней вперёд", show_alert=True)
        return

    await state.update_data(date=date_str)
    await state.set_state(BookingStates.choose_time)
    data = await _fsm_guard(callback, state, "master")
    if data is None:
        return
    master = data["master"]

    try:
        slots = await _get_available_slots(date_str, master)
    except Exception as e:
        logger.error(f"Failed to get available slots: {e}")
        slots = {t: "free" for t in _generate_time_slots(date_str)}

    # BUG-E FIX: Check if there are any free slots
    free_count = sum(1 for status in slots.values() if status == "free")
    busy_count = sum(1 for status in slots.values() if status == "busy")

    # HIGH-4 FIX: don't silently drop when no free slots — let
    # time_slots_kb render its waitlist + "Назад" fallback so the user
    # can either join the waitlist or pick another date. The earlier
    # show_alert + return left users stuck on the date-picker keyboard.
    date_formatted = keyboards._format_date(date_str)
    text = messages.date_selected(date_formatted)
    text = text.replace(
        "Выберите свободный слот:", f"Свободно: {free_count} | Занято: {busy_count}\n\nВыберите свободный слот:"
    )

    await _safe_edit(callback.message, text, reply_markup=keyboards.time_slots_kb(slots), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("time:"), BookingStates.choose_time)
async def cb_choose_time(callback: CallbackQuery, state: FSMContext):
    time_str = callback.data.split(":", 1)[1]
    data = await _fsm_guard(callback, state, "master", "date")
    if data is None:
        return
    date_str = data.get("date", "")
    master = data.get("master", "")

    # TASK-08: Check slot availability before accepting time selection
    try:
        available_slots = await _get_available_slots(date_str, master)
        slot_status = available_slots.get(time_str)

        if slot_status is None:
            # FIX: слот исчез из дикта — слишком мало времени до визита
            await callback.answer(
                f"Слот недоступен. Минимальное время до записи: {config.MIN_BOOKING_ADVANCE_MINUTES} мин.",
                show_alert=True,
            )
        elif slot_status != "free":
            await callback.answer(f"{E.CROSS} Этот слот уже занят! Пожалуйста, выберите другое время.", show_alert=True)
            # Refresh the slots display
            slots = await _get_available_slots(date_str, master)
            free_count = sum(1 for status in slots.values() if status == "free")
            busy_count = sum(1 for status in slots.values() if status == "busy")

            date_formatted = keyboards._format_date(date_str)
            text = messages.date_selected(date_formatted)
            text = text.replace(
                "Выберите свободный слот:", f"Свободно: {free_count} | Занято: {busy_count}\n\nВыберите свободный слот:"
            )

            try:
                await callback.message.edit_text(text, reply_markup=keyboards.time_slots_kb(slots), parse_mode="HTML")
            except Exception:
                pass
            return
    except Exception as e:
        logger.error(f"Failed to verify slot availability: {e}")

    available_slots = _generate_time_slots(data.get("date", ""))
    if time_str not in available_slots:
        await callback.answer(f"{P.CROSS} Неверное время", show_alert=True)
        return
    # Task 6: Блокируем слот на 5 мин — другие пользователи увидят его как занятый
    await storage.create_slot_lock(date_str, time_str, master)
    await state.update_data(time=time_str)
    await state.set_state(BookingStates.enter_name)

    # UX-002 FIX: Step 5 — шаг с подсказкой имени
    data_so_far = await state.get_data()
    text = f"{E.LIST} <b>Шаг 5 из 5 — Ваше имя</b>\n\n"
    text += "Вы записываетесь:\n"
    text += (
        f"{E.SCISSORS} {html.escape(data_so_far.get('master', ''))} — {html.escape(data_so_far.get('service', ''))}\n"
    )
    text += f"{E.CALENDAR} {keyboards._format_date(data_so_far.get('date', ''))} в {time_str}\n\n"
    text += f"{E.USER} <b>Как к вам обращаться?</b>\n"
    text += "Введите имя текстом ответным сообщением. Только буквы, без цифр и спецсимволов."

    tg_name = callback.from_user.first_name or ""
    await state.update_data(tg_name_suggestion=tg_name)
    name_rows = []
    if (
        tg_name
        and re.match(r"^[a-zA-Zа-яА-ЯёЁ\s\-\']+$", tg_name)
        and re.search(r"[a-zA-Zа-яА-ЯёЁ]", tg_name)
        and len(tg_name) <= 50
    ):
        name_rows.append([keyboards.icon_button(text=f"Использовать «{tg_name}»", callback_data="use_tg_name")])
    name_rows.append([keyboards.icon_button(text="Назад к времени", callback_data="back_to_time")])
    name_rows.append([keyboards.icon_button(text="Отменить", callback_data="cancel_booking")])
    back_kb = InlineKeyboardMarkup(inline_keyboard=name_rows)

    await _safe_edit(callback.message, text, reply_markup=back_kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "use_tg_name", BookingStates.enter_name)
async def cb_use_tg_name(callback: CallbackQuery, state: FSMContext):
    """Use Telegram first_name as booking name — FIX: callback, not message"""
    data = await state.get_data()
    name = data.get("tg_name_suggestion", "")
    if not name:
        await callback.answer(f"{P.CROSS} Имя не найдено, введите вручную", show_alert=True)
        return

    telegram_id = callback.from_user.id
    await state.update_data(name=name)
    data = await _fsm_guard(callback, state, "master", "date", "time", "service", "price")
    if data is None:
        return

    # CRIT-001/002 FIX: Apply loyalty discount and bonuses before saving
    base_price = data["price"]
    final_price, discount_info, bonus_spent = await _apply_discounts(telegram_id, base_price)

    booking = {
        "date": data["date"],
        "time": data["time"],
        "name": name,
        "telegram_id": telegram_id,
        "username": callback.from_user.username or "",
        "master": data["master"],
        "service": data["service"],
        "price": final_price,
    }

    try:
        # HIGH-2 FIX: pass bonus_spent into save_booking so the deduction
        # happens inside the same DB transaction as the INSERT. If the
        # transaction rolls back, the user's balance stays intact.
        booking_id = await storage.save_booking(booking, bonus_spent=bonus_spent)
    except Exception as e:
        logger.error(f"Failed to save booking: {e}")
        await callback.message.answer(messages.ERROR, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await state.clear()
        await callback.answer()
        return

    if not booking_id:
        # HIGH-2 FIX: release slot_lock so slot is immediately freed for other users
        _d2 = await state.get_data()
        await storage.release_slot_lock(_d2.get("date", ""), _d2.get("time", ""), _d2.get("master", ""))
        await callback.message.answer(messages.SLOT_BUSY, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await state.set_state(BookingStates.choose_time)
        await callback.answer()
        return

    # HIGH-2 FIX: bonus deduction already happened atomically inside save_booking.
    # The previous separate spend_bonus() call was racy (booking could be inserted
    # while bonuses remained unspent if the process crashed between the two calls).

    # BUG-S1+C5 FIX: use shared _finalize_booking (DRY + BOOKING_CONFIRMED template)
    bot = callback.bot
    await _finalize_booking(
        booking,
        booking_id,
        lambda text, kb, pm: callback.message.answer(text, reply_markup=kb, parse_mode=pm),
        bot,
        discount_info=discount_info,
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data.startswith("waitlist:"), BookingStates.choose_time)
async def cb_waitlist(callback: CallbackQuery, state: FSMContext):
    time_str = callback.data.split(":", 1)[1]
    data = await state.get_data()
    telegram_id = callback.from_user.id

    # BUG-012: Check if user already in waitlist for this slot
    data = await _fsm_guard(callback, state, "master", "date", "service")
    if data is None:
        return
    existing_waitlist = await storage.get_waitlist_for_slot(data["date"], time_str, data["master"])
    if any(wl["telegram_id"] == telegram_id and wl["status"] == "waiting" for wl in existing_waitlist):
        try:
            await callback.message.edit_text(
                f"{E.WARNING} Вы уже в листе ожидания на это время.\n\nМы уведомим вас, если слот освободится.",
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                f"{E.WARNING} Вы уже в листе ожидания на это время.\n\nМы уведомим вас, если слот освободится.",
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML",
            )
        await callback.answer()
        return

    # Task 15: Лимит 5 записей в листе ожидания
    _wl_count = await storage.get_user_waitlist_count(telegram_id)
    if _wl_count >= 5:
        await callback.answer("Максимум 5 записей в листе ожидания.", show_alert=True)
        return
    await storage.add_to_waitlist(
        telegram_id=telegram_id,
        name=callback.from_user.first_name or "",
        master=data["master"],
        service=data["service"],
        date=data["date"],
        time=time_str,
    )
    await _safe_edit(
        callback.message,
        messages.WAITLIST_ADDED.format(date=keyboards._format_date(data["date"]), time=time_str),
        reply_markup=keyboards.back_to_main_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BookingStates.enter_name)
async def handle_enter_name(message: Message, state: FSMContext):

    if not message.text or not message.text.strip():
        await message.answer(f"{E.CROSS} Пожалуйста, введите имя текстом.", parse_mode="HTML")
        return
    name = message.text.strip()

    # BUG-023: Improved name validation - must contain at least one letter
    # Matches: Анна, Анна-Мария, O'Brien, Jean-Claude, Мария Ивановна
    # Rejects: "12", "---", "   "
    if not name or len(name) > 50:
        await message.answer(f"{E.CROSS} Имя должно содержать от 1 до 50 символов.", parse_mode="HTML")
        return

    # Allow letters, spaces, hyphens, and apostrophes
    if not re.match(r"^[a-zA-Zа-яА-ЯёЁ\s\-\']+$", name):
        await message.answer(
            f"{E.CROSS} Имя может содержать только буквы, пробелы, дефисы и апострофы.", parse_mode="HTML"
        )
        return

    # BUG-023: Ensure at least one letter is present
    if not re.search(r"[a-zA-Zа-яА-ЯёЁ]", name):
        await message.answer(f"{E.CROSS} Имя должно содержать хотя бы одну букву.", parse_mode="HTML")
        return

    await state.update_data(name=name)
    data = await state.get_data()
    telegram_id = message.from_user.id

    # Task 1 FIX: создаём бронь сразу, без confirm-экрана (BookingStates.confirm не существует)
    data = await _fsm_guard(message, state, "master", "date", "time", "service", "price")
    if data is None:
        return
    # CRIT-001/002 FIX: Apply loyalty discount and bonuses before saving
    base_price = data["price"]
    final_price, discount_info, bonus_spent = await _apply_discounts(telegram_id, base_price)

    booking = {
        "date": data["date"],
        "time": data["time"],
        "name": name,
        "telegram_id": telegram_id,
        "username": message.from_user.username or "",
        "master": data["master"],
        "service": data["service"],
        "price": final_price,
    }

    try:
        # HIGH-2 FIX: pass bonus_spent into save_booking so the deduction
        # happens inside the same DB transaction as the INSERT. If the
        # transaction rolls back, the user's balance stays intact.
        booking_id = await storage.save_booking(booking, bonus_spent=bonus_spent)
    except Exception as e:
        logger.error(f"Failed to save booking: {e}")
        await message.answer(messages.ERROR, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await state.clear()
        return

    if not booking_id:
        # HIGH-2 FIX: release slot_lock so slot is immediately freed for other users
        _d = await state.get_data()
        await storage.release_slot_lock(_d.get("date", ""), _d.get("time", ""), _d.get("master", ""))
        await message.answer(messages.SLOT_BUSY, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await state.set_state(BookingStates.choose_time)
        return

    # HIGH-2 FIX: bonus deduction already happened atomically inside save_booking.
    # The previous separate spend_bonus() call was racy and could leave the user
    # with an unspent balance while the booking row existed.

    # BUG-S1+C5 FIX: use shared _finalize_booking
    bot = message.bot
    await _finalize_booking(
        booking,
        booking_id,
        lambda text, kb, pm: message.answer(text, reply_markup=kb, parse_mode=pm),
        bot,
        discount_info=discount_info,
    )
    await state.clear()


@router.callback_query(F.data == "confirm")
async def cb_confirm_deprecated(callback: CallbackQuery, state: FSMContext):
    """Task 1: Устаревший хендлер — запись создаётся в handle_enter_name.
    При нажатии старой кнопки — очищаем состояние и показываем сообщение."""
    # BUG-C1 FIX: Check FSM state before clearing to avoid destroying active booking session
    current_state = await state.get_state()
    if current_state is not None:
        await callback.answer("Вы в процессе записи. Завершите или отмените запись.", show_alert=True)
        return
    await state.clear()
    await _safe_edit(
        callback.message,
        f"{E.INFO} Сессия устарела. Начните запись заново.",
        reply_markup=keyboards.back_to_main_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_booking")
async def cb_cancel_booking(callback: CallbackQuery, state: FSMContext):
    # Task 6: Снять блокировку слота если пользователь вышел из потока записи
    data = await state.get_data()
    if data.get("date") and data.get("time") and data.get("master"):
        await storage.release_slot_lock(data["date"], data["time"], data["master"])
    await state.clear()
    # UX-005 FIX: Book again button after cancellation from FSM flow
    book_again_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [keyboards.icon_button(text="Записаться снова", callback_data="book")],
            [keyboards.icon_button(text="Главное меню", callback_data="main_menu")],
        ]
    )
    await callback.message.edit_text(messages.BOOKING_CANCELLED, reply_markup=book_again_kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "back_to_master")
async def cb_back_to_master(callback: CallbackQuery, state: FSMContext):
    # HIGH-8 FIX: release slot_lock if user had selected a time slot
    _bm_data = await state.get_data()
    if _bm_data.get("date") and _bm_data.get("time") and _bm_data.get("master"):
        await storage.release_slot_lock(_bm_data["date"], _bm_data["time"], _bm_data["master"])
    # Clear FSM data when going back
    await state.update_data(service=None, date=None, time=None, name=None)
    await state.set_state(BookingStates.choose_master)
    await _safe_edit(callback.message, messages.CHOOSE_MASTER, reply_markup=keyboards.masters_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "back_to_service")
async def cb_back_to_service(callback: CallbackQuery, state: FSMContext):
    # Clear FSM data when going back
    await state.update_data(date=None, time=None, name=None)
    await state.set_state(BookingStates.choose_service)
    data = await state.get_data()
    await _safe_edit(
        callback.message,
        messages.CHOOSE_SERVICE,
        reply_markup=await keyboards.services_kb(master_name=data.get("master", "")),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_date")
async def cb_back_to_date(callback: CallbackQuery, state: FSMContext):
    # FIX: освобождаем slot_lock при прыжке назад к дате
    _back_data = await state.get_data()
    if _back_data.get("date") and _back_data.get("time") and _back_data.get("master"):
        await storage.release_slot_lock(_back_data["date"], _back_data["time"], _back_data["master"])
    await state.update_data(time=None, name=None)
    await state.set_state(BookingStates.choose_date)
    data = await state.get_data()
    dates = await _get_next_dates(master_name=data.get("master", ""))

    text = messages.CHOOSE_DATE
    if data.get("service"):
        # Добавляем информацию о выбранной услуге.
        # CRIT-3 FIX (same class): service is admin-controlled; escape to keep
        # this banner safe even if the catalogue ever ships a malformed entry.
        text = (
            f"{E.CHECK} {html.escape(str(data['service']))} — {int(data.get('price', 0)):,} ₸\n\n".replace(",", " ")
            + text
        )

    await _safe_edit(callback.message, text, reply_markup=keyboards.dates_kb(dates), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "back_to_time")
async def cb_back_to_time(callback: CallbackQuery, state: FSMContext):
    # BUG-013: Clear FSM data when going back AND refresh slots
    data = await state.get_data()
    # FIX: освобождаем slot_lock при возврате назад
    if data.get("date") and data.get("time") and data.get("master"):
        await storage.release_slot_lock(data["date"], data["time"], data["master"])
    await state.update_data(name=None)
    await state.set_state(BookingStates.choose_time)
    data = await state.get_data()

    # Refresh slots from DB
    slots = await _get_available_slots(data.get("date", ""), data.get("master", ""))
    free_count = sum(1 for status in slots.values() if status == "free")
    busy_count = sum(1 for status in slots.values() if status == "busy")

    date_formatted = keyboards._format_date(data.get("date", ""))
    text = messages.date_selected(date_formatted)
    text = text.replace(
        "Выберите свободный слот:", f"Свободно: {free_count} | Занято: {busy_count}\n\nВыберите свободный слот:"
    )

    await _safe_edit(callback.message, text, reply_markup=keyboards.time_slots_kb(slots), parse_mode="HTML")
    await callback.answer()


# BUG-011 FIX: Handle no_slots callback
@router.callback_query(F.data == "no_slots")
async def cb_no_slots(callback: CallbackQuery):
    await callback.answer(
        f"{E.EMPTY} Все слоты на эту дату заняты. Попробуйте выбрать другую дату или встать в лист ожидания.",
        show_alert=True,
    )


@router.callback_query(F.data == "go_to_waitlist")
async def cb_go_to_waitlist(callback: CallbackQuery, state: FSMContext):
    """Navigate user to waitlist - showing busy slots"""
    data = await state.get_data()
    date_str = data.get("date", "")
    master = data.get("master", "")

    # Get all slots including busy ones
    slots = await _get_available_slots(date_str, master)
    busy_slots = {k: v for k, v in slots.items() if v == "busy"}

    if not busy_slots:
        await callback.answer(f"{P.EMPTY} Нет занятых слотов для листа ожидания", show_alert=True)
        return

    text = f"{E.RELOAD} <b>Лист ожидания</b>\n\n"
    text += "Выберите занятое время, на которое хотите встать в очередь:\n\n"
    text += f"{E.INFO} Мы уведомим вас, если это время освободится."

    # Show busy slots with waitlist: callback
    buttons = []
    busy_times = list(busy_slots.keys())
    for i in range(0, len(busy_times), 4):
        row = []
        for time_str in busy_times[i : i + 4]:
            row.append(keyboards.icon_button(text=f"{E.CROSS} {time_str}", callback_data=f"waitlist:{time_str}"))
        buttons.append(row)
    buttons.append([keyboards.icon_button(text="Назад", callback_data="back_to_time")])

    await _safe_edit(
        callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("remind_confirm:"))
async def cb_remind_confirm(callback: CallbackQuery, bot: Bot):
    booking_id = callback.data.split(":", 1)[1]
    # Notify admins that client confirmed attendance
    try:
        booking = await storage.get_booking_with_user(booking_id)
        if booking:
            notify_text = (
                f"{E.CHECK} <b>Клиент подтвердил визит</b>\n\n"
                f"{E.USER} {html.escape(booking['name'])} — "
                f"{keyboards._format_date(booking['date'])} в {booking['time']}\n"
                f"{E.SCISSORS} Мастер: {html.escape(booking['master'])}"
            )
            for admin_id in config.ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, notify_text, parse_mode="HTML")
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Failed to notify admin on remind_confirm: {e}")
    # Task 13: Обратная связь пользователю после подтверждения
    try:
        await callback.message.edit_text(
            f"{E.CHECK} <b>Отлично!</b> Ждём вас в указанное время.",
            reply_markup=keyboards.back_to_main_kb(),
            parse_mode="HTML",
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
        # Уведомляем админа если отмена менее чем за 2 часа
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
                    master=html.escape(booking["master"]),
                )
                for admin_id in config.ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, admin_text, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Failed to notify admin {admin_id} last-minute cancel: {e}")
        except Exception as e:
            logger.error(f"Last-minute cancel admin notify error: {e}")

        waitlist = await storage.get_waitlist_for_slot(booking["date"], booking["time"], booking["master"])
        for wl in waitlist:
            try:
                text = messages.WAITLIST_OFFER.format(
                    # CRIT-2/CRIT-3 FIX: master is admin/user-controlled and the
                    # template contains <b>/<tg-emoji> markup; escape and force
                    # parse_mode='HTML' so Telegram renders markup (not raw tags).
                    master=html.escape(str(booking["master"])),
                    date=keyboards._format_date(booking["date"]),
                    time=booking["time"],
                )
                await bot.send_message(wl["telegram_id"], text, parse_mode="HTML")
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
    # FIX: Use maxsplit to handle booking_ids containing colons
    parts = callback.data.split(":", 2)  # Split into max 3 parts
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

    # Save rating in FSM and ask for comment
    await state.update_data(review_booking_id=booking_id, review_rating=rating)
    await state.set_state(BookingStates.enter_review_comment)

    stars = f"{E.STAR}" * rating
    text = f"Спасибо за оценку! {stars}\n\n"
    text += f"{E.COMMENT} Хотите оставить комментарий?\n\n"
    text += "Напишите ваш отзыв или нажмите «Пропустить»:"

    await _safe_edit(callback.message, text, reply_markup=keyboards.skip_comment_kb(), parse_mode="HTML")
    await callback.answer()


@router.message(BookingStates.enter_review_comment)
async def handle_review_comment(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer(
            f"{E.CROSS} Пожалуйста, введите комментарий текстом или нажмите «Пропустить».", parse_mode="HTML"
        )
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
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"{E.WARNING} Вы уже оставляли отзыв на эту запись.", reply_markup=keyboards.back_to_main_kb()
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
                f"{E.CHECK} Спасибо за оценку! {E.STAR} {rating}/5", reply_markup=keyboards.back_to_main_kb()
            )
        except Exception:
            await callback.message.answer(
                f"{E.CHECK} Спасибо за оценку! {E.STAR} {rating}/5", reply_markup=keyboards.back_to_main_kb()
            )
    else:
        try:
            await callback.message.edit_text(
                f"{E.EXCLAMATION} Вы уже оставляли отзыв на эту запись.", reply_markup=keyboards.back_to_main_kb()
            )
        except Exception:
            await callback.message.answer(
                f"{E.EXCLAMATION} Вы уже оставляли отзыв на эту запись.", reply_markup=keyboards.back_to_main_kb()
            )

    await state.clear()
    await callback.answer()
