import re
import html
import logging
import csv
import json
import os
import asyncio
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import messages
import keyboards
import config
import storage
import scheduler
from utils import send_with_retry, edit_with_retry
from emoji_config import E

logger = logging.getLogger(__name__)

router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def _audit(admin_id: int, action: str, entity_type: str = "", entity_id: str = "", old_value: str = "", new_value: str = "") -> None:
    try:
        await storage.log_admin_action(admin_id, action, entity_type, entity_id, old_value, new_value)
    except Exception as e:
        logger.warning(f"Failed to write admin audit log: {e}")


def _parse_service_payload(text: str, fallback_duration: int | None = None) -> tuple[str, int, int]:
    parts = [part.strip() for part in text.split(",")]
    if len(parts) < 2:
        raise ValueError("format")
    if len(parts) == 2:
        name, raw_price = parts
        raw_duration = str(fallback_duration or config.DEFAULT_SERVICE_DURATION_MINUTES)
    else:
        name = ",".join(parts[:-2]).strip()
        raw_price = parts[-2]
        raw_duration = parts[-1]
    price = int(raw_price)
    duration = int(raw_duration)
    if duration <= 0 or duration % config.SLOT_STEP_MINUTES != 0:
        raise ValueError("duration")
    return name, price, duration


class AdminStates(StatesGroup):
    add_service = State()
    edit_service = State()
    change_address = State()
    change_phone = State()
    change_hours = State()
    change_salon_name = State()
    change_master_name = State()
    change_master_desc = State()
    change_master_exp = State()
    add_portfolio_photo = State()
    add_social_link = State()
    broadcast_message = State()
    add_unavailable_period = State()


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    if not _is_admin(message.from_user.id):
        await send_with_retry(message.bot, message.chat.id, messages.ADMIN_ONLY)
        return
    await send_with_retry(message.bot, message.chat.id, f"{E.LOCK} <b>Панель управления</b>", reply_markup=keyboards.admin_kb(), parse_mode="HTML")


def _parse_telegram_id_arg(text: str | None) -> int | None:
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        telegram_id = int(parts[1].strip())
    except ValueError:
        return None
    return telegram_id if telegram_id > 0 else None


@router.message(Command("privacy_export"))
async def cmd_privacy_export(message: Message):
    if not _is_admin(message.from_user.id):
        await send_with_retry(message.bot, message.chat.id, messages.ADMIN_ONLY)
        return
    telegram_id = _parse_telegram_id_arg(message.text)
    if not telegram_id:
        await send_with_retry(message.bot, message.chat.id, "Формат: /privacy_export <telegram_id>")
        return

    filepath = None
    try:
        data = await storage.export_client_data(telegram_id)
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        filepath = scripts_dir / f"privacy_export_{telegram_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        await message.answer_document(
            FSInputFile(str(filepath)),
            caption=f"{E.LIST} Экспорт данных клиента {telegram_id}",
        )
        await _audit(message.from_user.id, "privacy_export", "user", str(telegram_id))
    except Exception as e:
        logger.error(f"privacy_export failed: {e}")
        await send_with_retry(message.bot, message.chat.id, messages.ERROR)
    finally:
        if filepath and Path(filepath).exists():
            try:
                Path(filepath).unlink()
            except Exception as e:
                logger.warning(f"Failed to delete privacy export {filepath}: {e}")


@router.message(Command("privacy_delete"))
async def cmd_privacy_delete(message: Message):
    if not _is_admin(message.from_user.id):
        await send_with_retry(message.bot, message.chat.id, messages.ADMIN_ONLY)
        return
    telegram_id = _parse_telegram_id_arg(message.text)
    if not telegram_id:
        await send_with_retry(message.bot, message.chat.id, "Формат: /privacy_delete <telegram_id>")
        return

    try:
        counts = await storage.anonymize_client_data(telegram_id)
        await _audit(
            message.from_user.id,
            "privacy_delete",
            "user",
            str(telegram_id),
            new_value=json.dumps(counts, ensure_ascii=False, sort_keys=True),
        )
        text = (
            f"{E.CHECK} Данные клиента anonymized.\n\n"
            f"Записи: {counts.get('bookings_anonymized', 0)}\n"
            f"Лист ожидания: {counts.get('waitlist_anonymized', 0)}\n"
            f"Отзывы: {counts.get('reviews_anonymized', 0)}"
        )
        await send_with_retry(message.bot, message.chat.id, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"privacy_delete failed: {e}")
        await send_with_retry(message.bot, message.chat.id, messages.ERROR)


@router.callback_query(F.data == "admin")
async def cb_admin(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await edit_with_retry(callback.message, f"{E.LOCK} <b>Панель управления</b>", reply_markup=keyboards.admin_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        stats = await storage.get_stats()
        from tz_utils import get_now as _get_now
        today = _get_now(config.TIMEZONE).strftime("%Y-%m-%d")
        today_stats = await storage.get_bookings_summary(today)
        from monitoring import get_metrics_snapshot
        metrics = get_metrics_snapshot()
        # Fetch all users for stats (admin-only, low traffic)
        all_users = await storage.get_all_users()
        active_clients = sum(1 for u in all_users if not u.get("blocked"))

        text = f"{E.CHART} <b>Статистика</b>\n\n"
        text += f"<b>─── Общая ───</b>\n"
        text += f"{E.LIST} <b>Всего записей:</b> {stats['total']}\n"
        text += f"{E.CHECK} <b>Активных:</b> {stats['active']}\n"
        text += f"{E.CROSS} <b>Отменённых:</b> {stats['cancelled']}\n"
        text += f"{E.CHECK} <b>Завершённых:</b> {stats['completed']}\n"
        text += f"{E.MONEY} <b>Выручка:</b> {stats['revenue']:,} ₸\n".replace(",", " ")
        text += f"{E.USER} <b>Клиентов:</b> {len(all_users)} (активных: {active_clients})\n\n"

        text += f"<b>─── Сегодня ({today}) ───</b>\n"
        text += f"{E.LIST} {today_stats['total']} записей | "
        text += f"{E.CHECK} {today_stats['active']} активных | "
        text += f"{E.MONEY} {today_stats['revenue']:,} ₸\n".replace(",", " ")
        text += "\n"

        text += f"<b>─── Метрики системы ───</b>\n"
        text += f"{E.CLOCK} uptime: {metrics['uptime_human']}\n"
        text += f"{E.CROSS} ошибок: {metrics.get('errors_total', 0)} (rate: {metrics.get('error_rate_pct', 0):.1f}%)\n"
        text += f"{E.RELOAD} отмена: {metrics.get('cancel_rate_pct', 0):.1f}% от созданных\n"
        text += f"{E.STAR} рефералов: {metrics.get('referrals_completed', 0)}\n"
        text += f"{E.COMMENT} отзывов: {metrics.get('reviews_submitted', 0)}\n\n"

        by_service = await storage.get_stats_by_service()
        if by_service:
            text += f"<b>─── По услугам ───</b>\n"
            for s in by_service:
                text += f"• {s['service']}: {s['count']} зап. / {s['revenue']:,} ₸\n".replace(",", " ")

        await edit_with_retry(callback.message, text, reply_markup=keyboards.admin_kb(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in admin_stats: {e}")
        try:
            await edit_with_retry(callback.message, messages.ERROR, reply_markup=keyboards.admin_kb(), parse_mode="HTML")
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.broadcast_message)
    await edit_with_retry(
        callback.message,
        f"{E.PLANE} <b>Массовая рассылка</b>\n\n"
        "Отправьте текст сообщения. Оно уйдёт всем незаблокированным пользователям бота.",
        reply_markup=keyboards.back_to_main_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_audit")
async def cb_admin_audit(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        rows = await storage.get_admin_audit_log(limit=20)
        text = f"{E.LIST} <b>Аудит админ-действий</b>\n\n"
        if not rows:
            text += "Записей пока нет."
        for row in rows:
            entity = f" {html.escape(row.get('entity_type') or '')}:{html.escape(row.get('entity_id') or '')}".strip()
            text += (
                f"• {html.escape(row['created_at'][:16])} "
                f"admin={row['admin_id']} "
                f"{html.escape(row['action'])} {entity}\n"
            )
        await edit_with_retry(callback.message, text[:3900], reply_markup=keyboards.admin_kb(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in admin_audit: {e}")
        await callback.answer("Ошибка", show_alert=True)
        return
    await callback.answer()


def _format_unavailable_period(row: dict) -> str:
    period = f"{keyboards._format_date(row['date'])} {row['start_time']}-{row['end_time']}"
    reason = row.get("reason") or "без причины"
    return f"#{row['id']} {period} — {html.escape(reason)}"


@router.callback_query(F.data == "admin_unavailable")
async def cb_admin_unavailable(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    periods = await storage.get_unavailable_periods(limit=20)
    text = f"{E.CLOCK} <b>Блокировки времени</b>\n\n"
    if periods:
        for row in periods:
            text += f"• {_format_unavailable_period(row)}\n"
    else:
        text += f"{E.EMPTY} Блокировок пока нет."
    await edit_with_retry(callback.message, text, reply_markup=keyboards.admin_unavailable_kb(periods), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_add_unavailable")
async def cb_admin_add_unavailable(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.add_unavailable_period)
    await edit_with_retry(
        callback.message,
        f"{E.CLOCK} <b>Добавить блокировку времени</b>\n\n"
        "Форматы:\n"
        "2026-12-07\n"
        "2026-12-07, 10:00\n"
        "2026-12-07, 10:00, 12:00, причина",
        reply_markup=keyboards.back_to_main_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.add_unavailable_period)
async def handle_add_unavailable_period(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите блокировку текстом.")
        return

    parts = [part.strip() for part in message.text.split(",")]
    if len(parts) not in {1, 2, 3, 4}:
        await send_with_retry(message.bot, message.chat.id, "Неверный формат блокировки.")
        return

    date_str = parts[0]
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await send_with_retry(message.bot, message.chat.id, "Дата должна быть в формате YYYY-MM-DD.")
        return

    time_re = re.compile(r"^\d{2}:\d{2}$")
    start_time = parts[1] if len(parts) >= 2 and parts[1] else None
    end_time = parts[2] if len(parts) >= 3 and parts[2] else None
    reason = parts[3] if len(parts) == 4 else ""
    if start_time and not time_re.match(start_time):
        await send_with_retry(message.bot, message.chat.id, "Время начала должно быть в формате HH:MM.")
        return
    if end_time and not time_re.match(end_time):
        await send_with_retry(message.bot, message.chat.id, "Время окончания должно быть в формате HH:MM.")
        return

    try:
        period_id = await storage.add_unavailable_period(
            date_str,
            start_time=start_time,
            end_time=end_time,
            master=config.MASTER_NAME,
            reason=reason,
        )
    except ValueError as e:
        await send_with_retry(message.bot, message.chat.id, f"Неверный диапазон: {e}")
        return

    await _audit(
        message.from_user.id,
        "unavailable_add",
        "unavailable_period",
        str(period_id),
        new_value=message.text.strip(),
    )
    await state.clear()
    periods = await storage.get_unavailable_periods(limit=20)
    await send_with_retry(
        message.bot,
        message.chat.id,
        f"{E.CHECK} Блокировка добавлена.",
        reply_markup=keyboards.admin_unavailable_kb(periods),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_delete_unavailable:"))
async def cb_admin_delete_unavailable(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        period_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    deleted = await storage.delete_unavailable_period(period_id)
    if deleted:
        await _audit(callback.from_user.id, "unavailable_delete", "unavailable_period", str(period_id))
        await callback.answer("Блокировка удалена", show_alert=True)
    else:
        await callback.answer("Блокировка не найдена", show_alert=True)
    periods = await storage.get_unavailable_periods(limit=20)
    text = f"{E.CLOCK} <b>Блокировки времени</b>\n\n"
    if periods:
        for row in periods:
            text += f"• {_format_unavailable_period(row)}\n"
    else:
        text += f"{E.EMPTY} Блокировок пока нет."
    await edit_with_retry(callback.message, text, reply_markup=keyboards.admin_unavailable_kb(periods), parse_mode="HTML")


_broadcast_rate_limit = {
    "last_run_time": 0.0,
    "total_sent_today": 0,
}


@router.message(AdminStates.broadcast_message)
async def handle_admin_broadcast(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите текст рассылки.")
        return

    text = message.text.strip()
    if len(text) > 3500:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Текст слишком длинный. Максимум 3500 символов.", parse_mode="HTML")
        return

    # HIGH-01: Rate limiting for broadcast - max once per 5 minutes
    now = _time.time()
    if now - _broadcast_rate_limit["last_run_time"] < 300:
        remaining = int(300 - (now - _broadcast_rate_limit["last_run_time"]))
        await send_with_retry(
            message.bot, message.chat.id,
            f"{E.CROSS} Рассылку можно запускать раз в 5 минут.\nПодождите {remaining} секунд.",
            reply_markup=keyboards.admin_kb(),
            parse_mode="HTML"
        )
        return
    _broadcast_rate_limit["last_run_time"] = now

    users = await storage.get_all_users()
    total_users = len(users)
    if len(users) > config.MAX_BROADCAST_RECIPIENTS:
        users = users[:config.MAX_BROADCAST_RECIPIENTS]
        logger.warning(f"Broadcast: limiting to {config.MAX_BROADCAST_RECIPIENTS} of {total_users} users")
    if len(users) > config.MAX_BROADCAST_RECIPIENTS:
        users = users[:config.MAX_BROADCAST_RECIPIENTS]
    sent = 0
    failed = 0
    skipped = 0
    for user in users:
        if user.get("blocked"):
            skipped += 1
            continue
        try:
            await message.bot.send_message(user["telegram_id"], text)
            sent += 1
            await asyncio.sleep(0.05)
            # Telegram rate limit: max ~30 msg/sec, add small pause every 20 messages
            if sent % 20 == 0:
                await asyncio.sleep(0.5)
        except Exception as e:
            failed += 1
            logger.warning(f"Broadcast failed for {user.get('telegram_id')}: {e}")
            if hasattr(e, 'retry_after'):
                await asyncio.sleep(e.retry_after)

    await _audit(
        message.from_user.id,
        "broadcast",
        "users",
        "all",
        new_value=f"sent={sent}; failed={failed}; skipped={skipped}; text={text[:500]}",
    )
    await state.clear()
    await send_with_retry(
        message.bot,
        message.chat.id,
        f"{E.CHECK} Рассылка завершена.\n\nОтправлено: {sent}\nОшибок: {failed}\nПропущено заблокированных: {skipped}",
        reply_markup=keyboards.admin_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_bookings")
async def cb_admin_bookings(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await _show_admin_bookings_page(callback, offset=0)


@router.callback_query(F.data.startswith("admin_bookings_page:"))
async def cb_admin_bookings_page(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        offset = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        offset = 0
    await _show_admin_bookings_page(callback, offset=offset)


async def _show_admin_bookings_page(callback, offset: int = 0):
    PAGE = 5
    try:
        page_items, total = await storage.get_upcoming_bookings_paged(offset=offset, limit=PAGE)
        text = f"{E.LIST} Активные записи ({total} всего):\n\n"
        for b in page_items:
            text += f"{E.ID} <code>{b['id']}</code>: {keyboards._format_date(b['date'])} {b['time']}\n"
            text += f"   → {html.escape(b['name'])} / {html.escape(b['service'])}\n"
        if not page_items:
            text += f"{E.EMPTY} Нет активных записей.\n"
        kb_rows = []
        for b in page_items:
            kb_rows.append([InlineKeyboardButton(
                text=f"✏️ {b['id']} — {keyboards._format_date(b['date'])} {b['time']}",
                callback_data=f"admin_manage_booking:{b['id']}"
            )])
        nav_buttons = []
        if offset > 0:
            nav_buttons.append(InlineKeyboardButton(text="◄ Назад", callback_data=f"admin_bookings_page:{offset - PAGE}"))
        nav_buttons.append(InlineKeyboardButton(text=f"{offset // PAGE + 1}/{(total - 1) // PAGE + 1 if total else 1}", callback_data="noop"))
        if offset + PAGE < total:
            nav_buttons.append(InlineKeyboardButton(text="Дальше ►", callback_data=f"admin_bookings_page:{offset + PAGE}"))
        if nav_buttons:
            kb_rows.append(nav_buttons)
        kb_rows.append([InlineKeyboardButton(text="Назад в панель", callback_data="admin")])
        await edit_with_retry(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in admin_bookings: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_manage_booking:"))
async def cb_admin_manage_booking(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    booking_id = callback.data.split(":", 1)[1]
    shown = await _show_admin_booking(callback, booking_id)
    if shown:
        await callback.answer()


async def _show_admin_booking(callback: CallbackQuery, booking_id: str) -> bool:
    try:
        booking = await storage.get_booking_with_user(booking_id)
        if not booking or booking.get("status") != "active":
            await callback.answer("Запись не найдена или уже не активна", show_alert=True)
            return False
        text = (
            f"{E.ID} <b>Запись</b> <code>{booking_id}</code>\n\n"
            f"{E.USER} {html.escape(booking['name'])}\n"
            f"{E.PHONE} {html.escape(booking.get('phone') or 'телефон не указан')}\n"
            f"{E.BARBER} {html.escape(booking['service'])}\n"
            f"{E.CALENDAR} {keyboards._format_date(booking['date'])} в {booking['time']}\n"
            f"{E.CLOCK} Длительность: {storage.normalize_duration_minutes(booking.get('duration_minutes'))} мин\n"
            f"{E.MONEY} {booking['price']} ₸\n"
            f"{E.LOCK} Блокировка: {'да' if booking.get('user_blocked') else 'нет'}"
        )
        await edit_with_retry(
            callback.message, text,
            reply_markup=keyboards.admin_cancel_booking_kb(
                booking_id,
                telegram_id=booking.get("telegram_id"),
                user_blocked=bool(booking.get("user_blocked")),
            ),
            parse_mode="HTML"
        )
        return True
    except Exception as e:
        logger.error(f"Error in admin_manage_booking: {e}")
        await callback.answer("Ошибка", show_alert=True)
        return False


@router.callback_query(F.data.startswith("admin_user_block:"))
async def cb_admin_user_block(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        _, raw_user_id, action, booking_id = callback.data.split(":", 3)
        user_id = int(raw_user_id)
        blocked = action == "block"
        old_value = "blocked" if await storage.is_user_blocked(user_id) else "active"
        await storage.set_user_blocked(user_id, blocked)
        await _audit(
            callback.from_user.id,
            "user_block" if blocked else "user_unblock",
            "user",
            str(user_id),
            old_value=old_value,
            new_value="blocked" if blocked else "active",
        )
        await callback.answer("Клиент заблокирован" if blocked else "Клиент разблокирован", show_alert=True)
        await _show_admin_booking(callback, booking_id)
    except Exception as e:
        logger.error(f"Error in admin_user_block: {e}")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "admin_export")
async def cb_admin_export(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return

    filepath = None
    try:
        bookings = await asyncio.to_thread(lambda: storage.export_bookings_csv())
        filename = f"bookings_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        filepath = scripts_dir / filename

        try:
            cutoff = datetime.now() - timedelta(days=7)
            for old_file in scripts_dir.glob("bookings_export_*.csv"):
                try:
                    file_time = datetime.fromtimestamp(old_file.stat().st_mtime)
                    if file_time < cutoff:
                        old_file.unlink()
                        logger.info(f"Deleted old CSV: {old_file.name}")
                except Exception as e:
                    logger.warning(f"Failed to delete old CSV {old_file}: {e}")
        except Exception as e:
            logger.warning(f"Failed to cleanup old CSVs: {e}")

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "id", "date", "time", "master", "service", "price",
                    "duration_minutes", "status", "bonus_spent", "created_at",
                ],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(bookings)

        try:
            file = FSInputFile(str(filepath))
            await callback.message.answer_document(
                document=file,
                caption=f"Экспорт всех записей\nВсего: {len(bookings)} записей"
            )
            await _audit(callback.from_user.id, "bookings_export", "booking", "csv", new_value=f"rows={len(bookings)}")
            await edit_with_retry(
                callback.message,
                f"{E.CHECK} Экспорт завершён. Файл отправлен выше.",
                reply_markup=keyboards.admin_kb(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Failed to send CSV file: {e}")
            await edit_with_retry(
                callback.message,
                f"Экспорт завершён. Файл сохранён: {filename}",
                reply_markup=keyboards.admin_kb(),
            )
    except Exception as e:
        logger.error(f"Error in admin_export: {e}")
        try:
            await edit_with_retry(callback.message, messages.ERROR, reply_markup=keyboards.admin_kb())
        except Exception:
            pass
    finally:
        if filepath and Path(filepath).exists():
            try:
                Path(filepath).unlink()
                logger.info(f"Cleaned up CSV file: {filepath}")
            except Exception as e:
                logger.warning(f"Failed to delete CSV file {filepath}: {e}")

    await callback.answer()


@router.callback_query(F.data == "admin_services")
async def cb_admin_services(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        services_text = ""
        for service, price in config.SERVICES.items():
            services_text += f"{E.ARTIST_WOMAN} {service}: {price} ₸, {config.get_service_duration(service)} мин\n"
        await edit_with_retry(
            callback.message,
            f"{E.LIST} Услуги:\n\n{services_text}" if services_text else f"{E.LIST} Услуги:\n\n{E.EMPTY} Нет услуг.",
            reply_markup=keyboards.admin_services_kb(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error in admin_services: {e}")
    await callback.answer()


@router.callback_query(F.data == "admin_add_service")
async def cb_admin_add_service(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.add_service)
    await edit_with_retry(callback.message, messages.ADMIN_ADD_SERVICE_PROMPT)
    await callback.answer()


@router.message(AdminStates.add_service)
async def handle_add_service(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите данные текстом.")
        return
    text = message.text.strip()
    if "," not in text:
        await send_with_retry(message.bot, message.chat.id, "Неверный формат. Введите: Название, цена, длительность_мин")
        return
    try:
        name, price, duration = _parse_service_payload(text)
    except ValueError as e:
        if str(e) == "duration":
            await send_with_retry(message.bot, message.chat.id, f"Длительность должна быть положительной и кратной {config.SLOT_STEP_MINUTES} минутам.")
        else:
            await send_with_retry(message.bot, message.chat.id, "Неверный формат. Введите: Название, цена, длительность_мин")
        return

    if not name or len(name.encode("utf-8")) < 1 or len(name.encode("utf-8")) > 35:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Название услуги слишком длинное. Максимум ~17 символов кириллицей или 35 латиницей.", parse_mode="HTML")
        return

    if price <= 0:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Цена должна быть больше 0.", parse_mode="HTML")
        return

    config.SERVICES[name] = price
    config.SERVICE_DURATIONS[name] = duration
    try:
        await storage.save_service(name, price, duration)
        await config.save_config_to_db()
    except Exception as e:
        logger.error(f"Failed to save service to DB: {e}")
    await _audit(message.from_user.id, "service_add", "service", name, new_value=f"{price},{duration}")
    await state.clear()
    await send_with_retry(message.bot, message.chat.id, f"Услуга {html.escape(name)} добавлена ({price} ₸, {duration} мин).", reply_markup=keyboards.admin_kb(), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_service_detail:"))
async def cb_admin_service_detail(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    service_name = callback.data.split(":", 1)[1]
    price = config.SERVICES.get(service_name)
    if price is None:
        await callback.answer("Услуга не найдена", show_alert=True)
        return
    text = f"{service_name}: {price} ₸, {config.get_service_duration(service_name)} мин"
    await edit_with_retry(callback.message, text, reply_markup=keyboards.admin_service_detail_kb(service_name))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_edit_service:"))
async def cb_admin_edit_service(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    service_name = callback.data.split(":", 1)[1]
    await state.update_data(service_name=service_name)
    await state.set_state(AdminStates.edit_service)
    await edit_with_retry(callback.message, messages.ADMIN_EDIT_SERVICE_PROMPT)
    await callback.answer()


@router.message(AdminStates.edit_service)
async def handle_edit_service(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите данные текстом.")
        return
    data = await state.get_data()
    service_name = data["service_name"]
    old_price = config.SERVICES.get(service_name)
    old_duration = config.get_service_duration(service_name)
    text = message.text.strip()
    if "," not in text:
        await send_with_retry(message.bot, message.chat.id, "Неверный формат. Введите: Название, цена, длительность_мин")
        return
    try:
        name, price, duration = _parse_service_payload(text, fallback_duration=old_duration)
    except ValueError as e:
        if str(e) == "duration":
            await send_with_retry(message.bot, message.chat.id, f"Длительность должна быть положительной и кратной {config.SLOT_STEP_MINUTES} минутам.")
        else:
            await send_with_retry(message.bot, message.chat.id, "Неверный формат. Введите: Название, цена, длительность_мин")
        return

    if not name or len(name.encode("utf-8")) < 1 or len(name.encode("utf-8")) > 35:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Название услуги слишком длинное. Максимум ~17 символов кириллицей или 35 латиницей.", parse_mode="HTML")
        return

    if price <= 0:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Цена должна быть больше 0.", parse_mode="HTML")
        return

    if service_name in config.SERVICES:
        del config.SERVICES[service_name]
    if service_name in config.SERVICE_DURATIONS:
        del config.SERVICE_DURATIONS[service_name]
    config.SERVICES[name] = price
    config.SERVICE_DURATIONS[name] = duration
    try:
        await storage.remove_service(service_name)
        await storage.save_service(name, price, duration)
        await config.save_config_to_db()
    except Exception as e:
        logger.error(f"Failed to update service in DB: {e}")
    await _audit(
        message.from_user.id,
        "service_edit",
        "service",
        service_name,
        old_value=f"{service_name},{old_price},{old_duration}",
        new_value=f"{name},{price},{duration}",
    )
    await state.clear()
    await send_with_retry(message.bot, message.chat.id, f"Услуга {html.escape(name)} обновлена ({price} ₸, {duration} мин).", reply_markup=keyboards.admin_kb(), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_remove_service:"))
async def cb_admin_remove_service(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    service_name = callback.data.split(":", 1)[1]
    if service_name not in config.SERVICES:
        await callback.answer("Услуга не найдена", show_alert=True)
        return
    try:
        _svc_stats = await storage.get_service_stats(service_name)
        active_count = _svc_stats.get("active", 0)
        if active_count > 0:
            await callback.answer(
                f"Невозможно удалить услугу «{service_name}»: есть {active_count} активных записей. Сначала завершите или отмените их.",
                show_alert=True
            )
            return
    except Exception as e:
        logger.error(f"Failed to check active bookings for service: {e}")
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{E.CHECK} Да, удалить", callback_data=f"admin_confirm_remove_service:{service_name}"),
            InlineKeyboardButton(text=f"{E.CROSS} Отмена", callback_data=f"admin_service_detail:{service_name}"),
        ]
    ])
    await edit_with_retry(
        callback.message,
        f"Удалить услугу <b>{html.escape(service_name)}</b>?\n\nЭто действие нельзя отменить.",
        reply_markup=confirm_kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_confirm_remove_service:"))
async def cb_admin_confirm_remove_service(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    service_name = callback.data.split(":", 1)[1]
    if service_name in config.SERVICES:
        old_price = config.SERVICES.get(service_name)
        old_duration = config.get_service_duration(service_name)
        del config.SERVICES[service_name]
        config.SERVICE_DURATIONS.pop(service_name, None)
        try:
            await storage.remove_service(service_name)
            await config.save_config_to_db()
        except Exception as e:
            logger.error(f"Failed to remove service from DB: {e}")
        await _audit(callback.from_user.id, "service_remove", "service", service_name, old_value=f"{old_price},{old_duration}")
        await edit_with_retry(callback.message, f"{E.CROSS} Услуга {html.escape(service_name)} удалена.", reply_markup=keyboards.admin_kb(), parse_mode="HTML")
    else:
        await callback.answer("Услуга не найдена", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data == "admin_settings")
async def cb_admin_settings(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        text = (
            f"Настройки\n\n"
            f"{E.LABEL} Название: {config.SALON_NAME}\n"
            f"{E.LOCATION} Адрес: {config.SALON_ADDRESS}\n"
            f"{E.PHONE} Телефон: {config.SALON_PHONE}\n"
            f"{E.CLOCK} Часы работы: {config.SALON_WORKING_HOURS}\n"
            f"{E.WOMAN} Мастер: {config.MASTER_NAME}\n"
            f"{E.STAR} Опыт: {config.MASTER_EXPERIENCE}\n"
            f"{E.NOTE} Описание: {config.MASTER_DESCRIPTION}"
        )
        await edit_with_retry(callback.message, text, reply_markup=keyboards.admin_settings_kb())
    except Exception as e:
        logger.error(f"Error in admin_settings: {e}")
    await callback.answer()


@router.callback_query(F.data == "admin_change_address")
async def cb_admin_change_address(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.change_address)
    await edit_with_retry(callback.message, "Введите новый адрес:")
    await callback.answer()


@router.message(AdminStates.change_address)
async def handle_change_address(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите адрес текстом.")
        return
    address = message.text.strip()
    if len(address) > 200:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Адрес слишком длинный. Максимум 200 символов.", parse_mode="HTML")
        return
    old_value = config.SALON_ADDRESS
    config.SALON_ADDRESS = address
    try:
        await storage.save_settings("address", config.SALON_ADDRESS)
        await config.save_config_to_db()
    except Exception as e:
        logger.error(f"Failed to save address: {e}")
    await _audit(message.from_user.id, "settings_update", "settings", "address", old_value, address)
    await state.clear()
    await send_with_retry(message.bot, message.chat.id, "Адрес обновлён.", reply_markup=keyboards.admin_kb())


@router.callback_query(F.data == "admin_change_phone")
async def cb_admin_change_phone(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.change_phone)
    await edit_with_retry(callback.message, "Введите новый телефон:")
    await callback.answer()


@router.message(AdminStates.change_phone)
async def handle_change_phone(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите телефон текстом.")
        return
    phone = message.text.strip()
    if len(phone) > 200:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Телефон слишком длинный. Максимум 200 символов.", parse_mode="HTML")
        return
    old_value = config.SALON_PHONE
    config.SALON_PHONE = phone
    try:
        await storage.save_settings("phone", config.SALON_PHONE)
        await config.save_config_to_db()
    except Exception as e:
        logger.error(f"Failed to save phone: {e}")
    await _audit(message.from_user.id, "settings_update", "settings", "phone", old_value, phone)
    await state.clear()
    await send_with_retry(message.bot, message.chat.id, "Телефон обновлён.", reply_markup=keyboards.admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_change_hours")
async def cb_admin_change_hours(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.change_hours)
    await edit_with_retry(callback.message, "Введите новые часы работы (например: Пн-Сб: 10:00-21:00, Вс: 11:00-19:00):")
    await callback.answer()


@router.message(AdminStates.change_hours)
async def handle_change_hours(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите часы работы текстом.")
        return

    new_hours = message.text.strip()
    if len(new_hours) > 200:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Часы работы слишком длинные. Максимум 200 символов.", parse_mode="HTML")
        return

    try:
        time_patterns = re.findall(r'(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})', new_hours)
        if time_patterns:
            default_hours = (int(time_patterns[0][0]), int(time_patterns[0][2]))
            sunday_pattern = re.search(r'[Вв]с[:\s]+(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})', new_hours)
            if not sunday_pattern:
                sunday_pattern = re.search(r'[Ss]unday[:\s]+(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})', new_hours, re.IGNORECASE)
            sunday_hours = (int(sunday_pattern.group(1)), int(sunday_pattern.group(3))) if sunday_pattern else default_hours

            saturday_pattern = re.search(r'[Сс]б[:\s]+(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})', new_hours)
            if not saturday_pattern:
                saturday_pattern = re.search(r'[Ss]aturday[:\s]+(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})', new_hours, re.IGNORECASE)
            saturday_hours = (int(saturday_pattern.group(1)), int(saturday_pattern.group(3))) if saturday_pattern else default_hours

            config.WORKING_HOURS = {
                "monday": default_hours,
                "tuesday": default_hours,
                "wednesday": default_hours,
                "thursday": default_hours,
                "friday": default_hours,
                "saturday": saturday_hours,
                "sunday": sunday_hours,
            }

            start_h, end_h = default_hours
            if start_h >= end_h:
                await send_with_retry(
                    message.bot, message.chat.id,
                    f"{E.CROSS} Ошибка: время начала ({start_h}:00) должно быть меньше времени конца ({end_h}:00). Введите корректный диапазон.",
                    parse_mode="HTML"
                )
                return
            new_time_slots = []
            for h in range(start_h, end_h):
                new_time_slots.append(f"{h:02d}:00")
                new_time_slots.append(f"{h:02d}:30")

            if not new_time_slots:
                await send_with_retry(
                    message.bot, message.chat.id,
                    f"{E.CROSS} Не удалось сформировать временные слоты. Проверьте формат (например: 10:00-21:00).",
                    parse_mode="HTML"
                )
                return
            config.TIME_SLOTS = new_time_slots

            logger.info(f"Updated working hours: {config.WORKING_HOURS}")
            logger.info(f"Updated time slots: {config.TIME_SLOTS}")
    except Exception as e:
        logger.error(f"Failed to parse working hours: {e}")

    old_value = config.SALON_WORKING_HOURS
    config.SALON_WORKING_HOURS = new_hours
    try:
        await storage.save_settings("hours", config.SALON_WORKING_HOURS)
        await storage.save_settings("slots", ",".join(config.TIME_SLOTS))
        await config.save_config_to_db()
    except Exception as e:
        logger.error(f"Failed to save hours: {e}")
    await _audit(message.from_user.id, "settings_update", "settings", "hours", old_value, new_hours)
    await state.clear()
    if config.TIME_SLOTS:
        slots_info = f"Слоты: {len(config.TIME_SLOTS)} шт. (от {config.TIME_SLOTS[0]} до {config.TIME_SLOTS[-1]})"
    else:
        slots_info = "Слоты: не обновлены (не удалось распознать формат времени)"
    await send_with_retry(
        message.bot,
        message.chat.id,
        "Часы работы обновлены.\n\n"
        f"Новые часы: {config.SALON_WORKING_HOURS}\n"
        f"{slots_info}",
        reply_markup=keyboards.admin_kb()
    )


@router.callback_query(F.data == "admin_change_salon_name")
async def cb_admin_change_salon_name(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.change_salon_name)
    await edit_with_retry(callback.message, f"Введите новое название студии (сейчас: {config.SALON_NAME}):")
    await callback.answer()


@router.message(AdminStates.change_salon_name)
async def handle_change_salon_name(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите название текстом.")
        return
    name = message.text.strip()
    if len(name) > 100:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Название слишком длинное (макс 100 символов).", parse_mode="HTML")
        return
    old_value = config.SALON_NAME
    config.SALON_NAME = name
    try:
        await storage.save_settings("salon_name", name)
        await config.save_config_to_db()
    except Exception as e:
        logger.error(f"Failed to save salon name: {e}")
    await _audit(message.from_user.id, "settings_update", "settings", "salon_name", old_value, name)
    await state.clear()
    await send_with_retry(message.bot, message.chat.id, f"Название студии обновлено: {html.escape(name)}", reply_markup=keyboards.admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_change_master_name")
async def cb_admin_change_master_name(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.change_master_name)
    await edit_with_retry(callback.message, f"Введите новое имя мастера (сейчас: {config.MASTER_NAME}):")
    await callback.answer()


@router.message(AdminStates.change_master_name)
async def handle_change_master_name(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите имя текстом.")
        return
    name = message.text.strip()
    if len(name) > 50:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Имя слишком длинное (макс 50 символов).", parse_mode="HTML")
        return
    old_value = config.MASTER_NAME
    config.MASTER_NAME = name
    try:
        await storage.save_settings("master_name", name)
        await config.save_config_to_db()
    except Exception as e:
        logger.error(f"Failed to save master name: {e}")
    await _audit(message.from_user.id, "settings_update", "settings", "master_name", old_value, name)
    await state.clear()
    await send_with_retry(message.bot, message.chat.id, f"Имя мастера обновлено: {html.escape(name)}", reply_markup=keyboards.admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_change_master_desc")
async def cb_admin_change_master_desc(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.change_master_desc)
    await edit_with_retry(callback.message, f"Введите новое описание мастера (сейчас: {config.MASTER_DESCRIPTION}):")
    await callback.answer()


@router.message(AdminStates.change_master_desc)
async def handle_change_master_desc(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите описание текстом.")
        return
    desc = message.text.strip()
    if len(desc) > 500:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Описание слишком длинное (макс 500 символов).", parse_mode="HTML")
        return
    old_value = config.MASTER_DESCRIPTION
    config.MASTER_DESCRIPTION = desc
    try:
        await storage.save_settings("master_description", desc)
        await config.save_config_to_db()
    except Exception as e:
        logger.error(f"Failed to save master description: {e}")
    await _audit(message.from_user.id, "settings_update", "settings", "master_description", old_value, desc)
    await state.clear()
    await send_with_retry(message.bot, message.chat.id, "Описание мастера обновлено.", reply_markup=keyboards.admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_change_master_exp")
async def cb_admin_change_master_exp(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.change_master_exp)
    await edit_with_retry(callback.message, f"Введите новый стаж мастера (сейчас: {config.MASTER_EXPERIENCE}):")
    await callback.answer()


@router.message(AdminStates.change_master_exp)
async def handle_change_master_exp(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите стаж текстом.")
        return
    exp = message.text.strip()
    if len(exp) > 50:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Стаж слишком длинный (макс 50 символов).", parse_mode="HTML")
        return
    old_value = config.MASTER_EXPERIENCE
    config.MASTER_EXPERIENCE = exp
    try:
        await storage.save_settings("master_experience", exp)
        await config.save_config_to_db()
    except Exception as e:
        logger.error(f"Failed to save master experience: {e}")
    await _audit(message.from_user.id, "settings_update", "settings", "master_experience", old_value, exp)
    await state.clear()
    await send_with_retry(message.bot, message.chat.id, f"Стаж мастера обновлён: {html.escape(exp)}", reply_markup=keyboards.admin_kb(), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_pre_cancel:"))
async def cb_admin_pre_cancel(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    booking_id = callback.data.split(":", 1)[1]
    try:
        booking = await storage.get_booking_with_user(booking_id)
        if not booking or booking.get("status") != "active":
            await callback.answer("Запись не найдена или уже не активна", show_alert=True)
            return
        confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"{E.CROSS} Да, отменить", callback_data=f"admin_cancel:{booking_id}"),
                InlineKeyboardButton(text="Нет, вернуться", callback_data=f"admin_manage_booking:{booking_id}"),
            ]
        ])
        await edit_with_retry(
            callback.message,
            f"Отменить запись <code>{booking_id}</code> клиента {html.escape(booking['name'])}?",
            reply_markup=confirm_kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in admin_pre_cancel: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_cancel:"))
async def cb_admin_cancel_booking(callback: CallbackQuery, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    booking_id = callback.data.split(":", 1)[1]
    try:
        booking = await storage.admin_cancel_booking(booking_id)
        if booking:
            await scheduler.cancel_reminders(booking_id)
            await _audit(callback.from_user.id, "booking_cancel", "booking", booking_id, old_value="active", new_value="cancelled")
            await edit_with_retry(
                callback.message,
                f"{E.CHECK} {E.CROSS} {E.ID} <b>Запись {booking_id} отменена администратором.</b>",
                reply_markup=keyboards.admin_kb(),
                parse_mode="HTML"
            )
            waitlist = await storage.get_waitlist_for_open_period(
                booking["date"], config.MASTER_NAME, booking["time"], booking.get("duration_minutes")
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
            await callback.answer("Запись не найдена или уже не активна", show_alert=True)
    except Exception as e:
        logger.error(f"Error in admin_cancel: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_complete_booking:"))
async def cb_admin_complete_booking(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    booking_id = callback.data.split(":", 1)[1]
    try:
        booking = await storage.admin_complete_booking(booking_id)
        if booking:
            await scheduler.cancel_reminders(booking_id)
            try:
                await storage.update_loyalty(booking["telegram_id"], booking["name"])
            except Exception as e:
                logger.error(f"Failed to update loyalty on completion: {e}")
            await _audit(callback.from_user.id, "booking_complete", "booking", booking_id, old_value="active", new_value="completed")
            await edit_with_retry(
                callback.message,
                f"{E.CHECK} {E.USER} <b>Запись {booking_id} завершена.</b>",
                reply_markup=keyboards.admin_kb(),
                parse_mode="HTML"
            )
        else:
            await callback.answer("Запись не найдена или уже не активна", show_alert=True)
    except Exception as e:
        logger.error(f"Error in admin_complete_booking: {e}")
    await callback.answer()


# ===== ПОРТФОЛИО (АДМИН) =====

@router.callback_query(F.data == "admin_portfolio")
async def cb_admin_portfolio(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.clear()
    await edit_with_retry(
        callback.message,
        messages.ADMIN_PORTFOLO_INTRO,
        reply_markup=keyboards.portfolio_admin_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_add_portfolio_photo")
async def cb_admin_add_portfolio_photo(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.add_portfolio_photo)
    await edit_with_retry(
        callback.message,
        messages.ADMIN_PORTFOLIO_ADD_PROMPT,
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminStates.add_portfolio_photo)
async def handle_add_portfolio_photo(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.photo:
        await send_with_retry(
            message.bot, message.chat.id,
            f"{E.CROSS} Пожалуйста, отправьте фото.",
            reply_markup=keyboards.portfolio_admin_kb(),
            parse_mode="HTML"
        )
        return

    current_count = await storage.count_portfolio_photos()
    if current_count >= config.MAX_PORTFOLIO_PHOTOS:
        await send_with_retry(
            message.bot,
            message.chat.id,
            f"{E.CROSS} Достигнут лимит портфолио: {config.MAX_PORTFOLIO_PHOTOS} фото.",
            reply_markup=keyboards.portfolio_admin_kb(),
            parse_mode="HTML",
        )
        return

    photo = message.photo[-1]
    if photo.file_size and photo.file_size > config.MAX_PORTFOLIO_PHOTO_SIZE_BYTES:
        max_mb = config.MAX_PORTFOLIO_PHOTO_SIZE_BYTES / 1024 / 1024
        await send_with_retry(
            message.bot,
            message.chat.id,
            f"{E.CROSS} Фото слишком большое. Максимум: {max_mb:.1f} МБ.",
            reply_markup=keyboards.portfolio_admin_kb(),
            parse_mode="HTML",
        )
        return

    file_id = photo.file_id
    caption = message.caption or ""
    if len(caption) > 500:
        caption = caption[:500]

    try:
        photo_id = await storage.add_portfolio_photo(file_id, caption)
        await _audit(message.from_user.id, "portfolio_photo_add", "portfolio_photo", str(photo_id), new_value=caption[:500])
        await send_with_retry(
            message.bot, message.chat.id,
            f"{E.CHECK} Фото добавлено в портфолио (ID: {photo_id}).",
            reply_markup=keyboards.portfolio_admin_kb(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to add portfolio photo: {e}")
        await send_with_retry(
            message.bot, message.chat.id,
            messages.ERROR,
            reply_markup=keyboards.portfolio_admin_kb(),
            parse_mode="HTML"
        )
    await state.clear()


@router.callback_query(F.data == "admin_delete_portfolio_photo")
async def cb_admin_delete_portfolio_photo(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        photos = await storage.get_portfolio_photos(limit=100, offset=0)
        if not photos:
            await edit_with_retry(
                callback.message,
                messages.ADMIN_PORTFOLIO_DELETE_EMPTY,
                reply_markup=keyboards.portfolio_admin_kb(),
                parse_mode="HTML"
            )
            await callback.answer()
            return
        await edit_with_retry(
            callback.message,
            "Выберите фото для удаления:",
            reply_markup=keyboards.portfolio_delete_list_kb(photos),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in admin_delete_portfolio_photo: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_confirm_delete_photo:"))
async def cb_admin_confirm_delete_photo(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        photo_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный ID фото", show_alert=True)
        return
    await edit_with_retry(
        callback.message,
        messages.ADMIN_PORTFOLIO_DELETE_CONFIRM.format(photo_id=photo_id),
        reply_markup=keyboards.confirm_delete_photo_kb(photo_id),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_delete_photo_confirm:"))
async def cb_admin_delete_photo_confirm(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        photo_id = int(callback.data.split(":", 1)[1])
        deleted = await storage.delete_portfolio_photo(photo_id)
        if deleted:
            await _audit(callback.from_user.id, "portfolio_photo_delete", "portfolio_photo", str(photo_id))
            text = messages.ADMIN_PORTFOLIO_DELETED.format(photo_id=photo_id)
        else:
            text = f"{E.CROSS} Фото #{photo_id} не найдено."
        await edit_with_retry(
            callback.message, text,
            reply_markup=keyboards.portfolio_admin_kb(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in admin_delete_photo_confirm: {e}")
    await callback.answer()


# ===== СОЦИАЛЬНЫЕ СЕТИ (АДМИН) =====

@router.callback_query(F.data == "admin_social_links")
async def cb_admin_social_links(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.clear()
    try:
        links = await storage.get_social_links()
        if links:
            text = f"{E.LINK} <b>Социальные сети</b>\n\n"
            for link in links:
                text += f"• {html.escape(link['platform'])}: {html.escape(link['url'])}\n"
        else:
            text = messages.ADMIN_SOCIAL_LINKS_INTRO
        await edit_with_retry(
            callback.message, text,
            reply_markup=keyboards.social_links_admin_kb(links),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in admin_social_links: {e}")
    await callback.answer()


@router.callback_query(F.data == "admin_add_social_link")
async def cb_admin_add_social_link(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AdminStates.add_social_link)
    await edit_with_retry(
        callback.message,
        messages.ADMIN_SOCIAL_ADD_PROMPT,
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminStates.add_social_link)
async def handle_add_social_link(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await send_with_retry(message.bot, message.chat.id, "Введите данные текстом.")
        return
    text = message.text.strip()
    if "," not in text:
        await send_with_retry(message.bot, message.chat.id, "Неверный формат. Введите: Название, URL")
        return
    parts = text.split(",", 1)
    platform = parts[0].strip()
    url = parts[1].strip()

    if not platform or len(platform) > 50:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} Название должно быть от 1 до 50 символов.", parse_mode="HTML")
        return
    if not url.startswith(("http://", "https://")) or len(url) > 500:
        await send_with_retry(message.bot, message.chat.id, f"{E.CROSS} URL должен начинаться с http:// или https:// (макс 500 символов).", parse_mode="HTML")
        return

    try:
        await storage.add_social_link(platform, url)
        await _audit(message.from_user.id, "social_link_add", "social_link", platform, new_value=url)
        links = await storage.get_social_links()
        await send_with_retry(
            message.bot, message.chat.id,
            f"{E.CHECK} Ссылка добавлена.",
            reply_markup=keyboards.social_links_admin_kb(links),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to add social link: {e}")
        await send_with_retry(
            message.bot, message.chat.id,
            messages.ERROR,
            reply_markup=keyboards.admin_kb(),
            parse_mode="HTML"
        )
    await state.clear()


@router.callback_query(F.data.startswith("admin_delete_social_link:"))
async def cb_admin_delete_social_link(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(messages.ADMIN_ONLY, show_alert=True)
        return
    try:
        link_id = int(callback.data.split(":", 1)[1])
        link = await storage.get_social_link_by_id(link_id)
        await storage.delete_social_link(link_id)
        await _audit(
            callback.from_user.id,
            "social_link_delete",
            "social_link",
            str(link_id),
            old_value=f"{link.get('platform')}: {link.get('url')}" if link else "",
        )
        links = await storage.get_social_links()
        await edit_with_retry(
            callback.message,
            messages.ADMIN_SOCIAL_LINK_DELETED,
            reply_markup=keyboards.social_links_admin_kb(links),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in admin_delete_social_link: {e}")
    await callback.answer()
