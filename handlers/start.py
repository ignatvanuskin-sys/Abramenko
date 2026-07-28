import re
import html
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import messages
import keyboards
import config
import storage
import scheduler
from utils import send_with_retry, edit_with_retry, notify_admins
from emoji_config import E, P

logger = logging.getLogger(__name__)

router = Router()


def _booking_duration_text(booking: dict) -> str:
    duration = storage.normalize_duration_minutes(
        booking.get("duration_minutes") or config.get_service_duration(booking.get("service", ""))
    )
    return f"{duration} мин"


class ContactStates(StatesGroup):
    waiting_contact = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    telegram_id = message.from_user.id
    
    # TASK-07: Referral system - handle /start ref_CODE
    ref_code = None
    if message.text and len(message.text.split()) > 1:
        args = message.text.split()[1:]
        if len(args) > 0 and args[0].startswith("ref_"):
            ref_code = args[0][4:]  # Remove "ref_" prefix
            logger.info(f"User {telegram_id} started with referral code: {ref_code}")
    
    user = await storage.get_user(telegram_id)
    if not user:
        await storage.save_user(
            telegram_id,
            username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )
        
        # TASK-07: Process referral if this is a new user
        if ref_code:
            try:
                # Find referrer by ref_code
                referrer = await storage.get_user_by_ref_code(ref_code)
                if referrer and referrer["telegram_id"] != telegram_id:
                    # Add referral relationship
                    success = await storage.add_referral(referrer["telegram_id"], telegram_id)
                    if success:
                        # Award bonus to referrer
                        await storage.add_bonus(referrer["telegram_id"], config.REFERRAL_BONUS)
                        logger.info(f"Referral successful: {referrer['telegram_id']} referred {telegram_id}")
                        
                        # Notify referrer
                        try:
                            referrer_text = (
                                f"{E.STAR} <b>Новый реферал!</b>\n\n"
                                f"Пользователь {html.escape(message.from_user.first_name or 'Гость')} "
                                f"зарегистрировался по вашей ссылке.\n\n"
                                f"Вы получили {config.REFERRAL_BONUS} бонусов!"
                            )
                            await message.bot.send_message(
                                referrer["telegram_id"],
                                referrer_text,
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.error(f"Failed to notify referrer: {e}")
                        
                        # Notify new user
                        await send_with_retry(
                            message.bot, message.chat.id,
                            f"{E.CHECK} <b>Добро пожаловать!</b>\n\n"
                            f"Вы зарегистрировались по реферальной ссылке.\n"
                            f"Ваш друг получил {config.REFERRAL_BONUS} бонусов!",
                            parse_mode="HTML"
                        )
            except Exception as e:
                logger.error(f"Failed to process referral: {e}")

    # TASK-10: Remove phone barrier - show main menu immediately, ask for phone later
    text = messages.welcome_text(config.SALON_NAME)
    await send_with_retry(
        message.bot, message.chat.id,
        text,
        reply_markup=keyboards.main_menu_kb(),
        parse_mode="HTML"
    )
    return


@router.message(ContactStates.waiting_contact)
async def handle_contact(message: Message, state: FSMContext):
    phone = None
    if message.contact:
        from utils import validate_phone
        valid, result = validate_phone(message.contact.phone_number)
        if not valid:
            await send_with_retry(
                message.bot, message.chat.id,
                f"{E.CROSS} Номер из контакта не соответствует ожидаемому формату.\n"
                f"Введите в формате: <code>+7 700 123 45 67</code>.",
                parse_mode="HTML",
            )
            return
        phone = result
    elif message.text:
        # MED-001 FIX: Allow manual phone number input with validation
        from utils import validate_phone
        valid, result = validate_phone(message.text)
        if not valid:
            await send_with_retry(
                message.bot, message.chat.id,
                f"{E.CROSS} {result}\n"
                f"Или нажмите кнопку «Поделиться номером».",
                parse_mode="HTML",
            )
            return
        phone = result
    else:
        await send_with_retry(
            message.bot, message.chat.id,
            f"{E.CROSS} Пожалуйста, введите номер телефона или нажмите кнопку «Поделиться номером».",
            parse_mode="HTML",
        )
        return

    if phone:
        await storage.save_user(
            message.from_user.id,
            phone=phone,
            username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )

    await state.clear()
    # BUG-PHONE FIX: сначала убираем ReplyKeyboard (кнопку шаринга номера), затем показываем inline-меню
    await send_with_retry(
        message.bot, message.chat.id,
        f"{E.CHECK} <b>Номер сохранён!</b>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await send_with_retry(
        message.bot, message.chat.id,
        messages.MAIN_MENU_TEXT,
        reply_markup=keyboards.main_menu_kb(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "share_phone")
async def cb_share_phone(callback: CallbackQuery, state: FSMContext):
    # Only Telegram contact share button
    text = f"{E.PHONE} <b>Поделитесь номером телефона</b>\n\n"
    text += "Нажмите кнопку ниже, чтобы бот получил ваш номер."
    
    await callback.message.answer(
        text,
        reply_markup=keyboards.phone_kb(),
        parse_mode="HTML"
    )
    await state.set_state(ContactStates.waiting_contact)
    await callback.answer()


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit_with_retry(
        callback.message,
        messages.MAIN_MENU_TEXT,
        reply_markup=keyboards.main_menu_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "my_bookings")
async def cb_my_bookings(callback: CallbackQuery):
    bookings = await storage.get_user_bookings(callback.from_user.id)
    if not bookings:
        from emoji_config import P
        await callback.answer(f"{P.EMPTY} У вас нет активных записей", show_alert=True)
        return
    
    text = f"{E.LIST} <b>Ваши активные записи:</b>\n\n"
    for i, b in enumerate(bookings, 1):
        date_str = keyboards._format_date(b['date'])
        text += f"<b>{i}. {date_str} в {b['time']}</b>\n"
        text += f"   {E.MASTER} Мастер: {b['master']}\n"
        text += f"   {E.BARBER} Услуга: {b['service']}\n"
        text += f"   {E.CLOCK} Длительность: {_booking_duration_text(b)}\n"
        text += f"   {E.MONEY} Цена: {b['price']:,} ₸\n".replace(",", " ")
        text += f"   {E.ID} ID: <code>{b['id']}</code>\n\n"
    
    text += f"{E.POINT_DOWN} Выберите запись для подробностей:"
    
    await callback.message.edit_text(text, reply_markup=keyboards.bookings_list_kb(bookings), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("booking_detail:"))
async def cb_booking_detail(callback: CallbackQuery):
    booking_id = callback.data.split(":", 1)[1]
    
    # Получаем информацию о записи
    bookings = await storage.get_user_bookings(callback.from_user.id)
    booking = next((b for b in bookings if b['id'] == booking_id), None)
    
    if not booking:
        from emoji_config import P
        await callback.answer(f"{P.CROSS} Запись не найдена", show_alert=True)
        return
    
    date_str = keyboards._format_date(booking['date'])
    text = f"{E.LIST} <b>Детали записи</b>\n\n"
    text += f"{E.CALENDAR} <b>Дата:</b> {date_str}\n"
    text += f"{E.CLOCK} <b>Время:</b> {booking['time']}\n"
    text += f"{E.CLOCK} <b>Длительность:</b> {_booking_duration_text(booking)}\n"
    text += f"{E.MASTER} <b>Мастер:</b> {booking['master']}\n"
    text += f"{E.BARBER} <b>Услуга:</b> {booking['service']}\n"
    text += f"{E.MONEY} <b>Цена:</b> {booking['price']:,} ₸\n".replace(",", " ")
    text += f"{E.ID} <b>ID:</b> <code>{booking['id']}</code>\n\n"
    text += f"{E.WARNING} Хотите отменить эту запись?"
    
    await callback.message.edit_text(text, reply_markup=keyboards.booking_detail_kb(booking_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_book:"))
async def cb_cancel_book(callback: CallbackQuery):
    """BUG-FIX: Handler for cancel_book: callbacks from cancel_bookings_kb (/cancel command).
    Was missing => caused 'Update is not handled' and button endlessly loading.
    """
    booking_id = callback.data.split(":", 1)[1]
    bookings = await storage.get_user_bookings(callback.from_user.id)
    booking = next((b for b in bookings if b['id'] == booking_id), None)

    if not booking:
        from emoji_config import P
        await callback.answer(f"{P.CROSS} Запись не найдена", show_alert=True)
        return

    date_str = keyboards._format_date(booking['date'])
    text = f"{E.WARNING} <b>Подтвердите отмену</b>\n\n"
    text += f"Вы действительно хотите отменить запись?\n\n"
    text += f"{E.CALENDAR} <b>Дата:</b> {date_str}\n"
    text += f"{E.CLOCK} <b>Время:</b> {booking['time']}\n"
    text += f"{E.MASTER} <b>Мастер:</b> {booking['master']}\n"
    text += f"{E.BARBER} <b>Услуга:</b> {booking['service']}\n\n"
    text += f"{E.IDEA} <b>Это действие нельзя отменить!</b>"

    try:
        await callback.message.edit_text(text, reply_markup=keyboards.confirm_cancel_kb(booking_id), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboards.confirm_cancel_kb(booking_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("ask_cancel:"))
async def cb_ask_cancel(callback: CallbackQuery):
    # TASK-06: Show warning before cancellation
    booking_id = callback.data.split(":", 1)[1]
    bookings = await storage.get_user_bookings(callback.from_user.id)
    booking = next((b for b in bookings if b['id'] == booking_id), None)
    
    if not booking:
        from emoji_config import P
        await callback.answer(f"{P.CROSS} Запись не найдена", show_alert=True)
        return
    
    date_str = keyboards._format_date(booking['date'])
    text = f"{E.WARNING} <b>Подтвердите отмену</b>\n\n"
    text += f"Вы действительно хотите отменить запись?\n\n"
    text += f"{E.CALENDAR} <b>Дата:</b> {date_str}\n"
    text += f"{E.CLOCK} <b>Время:</b> {booking['time']}\n"
    text += f"{E.MASTER} <b>Мастер:</b> {booking['master']}\n"
    text += f"{E.BARBER} <b>Услуга:</b> {booking['service']}\n\n"
    text += f"{E.IDEA} <b>Это действие нельзя отменить!</b>"
    
    await callback.message.edit_text(text, reply_markup=keyboards.confirm_cancel_kb(booking_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_cancel:"))
async def cb_confirm_cancel(callback: CallbackQuery):
    booking_id = callback.data.split(":", 1)[1]
    booking = await storage.cancel_booking(booking_id, telegram_id=callback.from_user.id)
    
    # BUG-005 FIX: Removed duplicate callback.answer() at the end
    if booking:
        await scheduler.cancel_reminders(booking_id)
        # Возвращаем бонусы если были потрачены
        bonus_spent = booking.get('bonus_spent', 0) or 0
        if bonus_spent > 0:
            try:
                await storage.add_bonus(booking['telegram_id'], bonus_spent)
                logger.info(f"Refunded {bonus_spent} bonuses to user {booking['telegram_id']} on cancel")
            except Exception as _be:
                logger.error(f"Failed to refund bonuses on cancel: {_be}")
        date_str = keyboards._format_date(booking['date'])
        text = f"{E.CHECK} <b>Запись отменена!</b>\n\n"
        text += f"{E.CALENDAR} {date_str} в {booking['time']}\n"
        text += f"{E.MASTER} Мастер: {booking['master']}\n"
        text += f"{E.BARBER} Услуга: {booking['service']}\n\n"
        text += f"{E.IDEA} Вы можете записаться снова в любое время!"
        
        await callback.message.edit_text(text, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        await callback.answer()  # Single answer here
        
        # Уведомляем мастера и администраторов об отмене
        bot = callback.bot
        
        # BUG-019: Add html.escape and parse_mode for admin notifications
        cancel_text = f"{E.CROSS} <b>Отмена записи</b>\n\n"
        cancel_text += f"{E.USER} Клиент: {html.escape(booking['name'])}\n"
        cancel_text += f"{E.CALENDAR} {date_str} в {booking['time']}\n"
        cancel_text += f"{E.MASTER} Мастер: {html.escape(booking['master'])}\n"
        cancel_text += f"{E.BARBER} Услуга: {html.escape(booking['service'])}"
        
        await notify_admins(bot, cancel_text)
        
        # Проверяем лист ожидания
        waitlist = await storage.get_waitlist_for_open_period(
            booking["date"], booking["master"], booking["time"], booking.get("duration_minutes")
        )
        for wl in waitlist:
            try:
                wl_text = f"{E.STAR} <b>Освободилось время!</b>\n\n"
                wl_text += f"{E.CALENDAR} {date_str} в {booking['time']}\n"
                wl_text += f"{E.MASTER} Мастер: {booking['master']}\n\n"
                wl_text += f"{E.IDEA} Вы можете записаться на это время!"
                await bot.send_message(wl["telegram_id"], wl_text, parse_mode="HTML")
                await storage.update_waitlist_status(wl["id"], "offered")
            except Exception as e:
                logger.error(f"Failed to notify waitlist {wl['telegram_id']}: {e}")
    else:
        from emoji_config import P
        await callback.answer(f"{P.CROSS} Запись не найдена или уже отменена", show_alert=True)


@router.message(Command("me"))
async def cmd_me(message: Message):
    user = await storage.get_user(message.from_user.id)
    if not user:
        await send_with_retry(message.bot, message.chat.id, messages.ERROR, parse_mode="HTML")
        return

    bookings = await storage.get_user_bookings(message.from_user.id)
    
    text = f"{E.USER} <b>Ваш профиль</b>\n\n"
    text += f"<b>Имя:</b> {user['first_name']}\n"
    text += f"<b>Телефон:</b> {user['phone'] or 'не указан'}\n"
    text += f"<b>Активных записей:</b> {len(bookings)}\n\n"

    loyalty = await storage.get_loyalty(message.from_user.id)
    if loyalty:
        text += f"{E.STAR} <b>Программа лояльности:</b>\n"
        text += f"   • Визитов: {loyalty['visits']}\n"
        text += f"   • Бонусов: {loyalty['bonuses']}\n"
        
        # TASK-07: Show referral code and link
        if loyalty.get('ref_code'):
            bot_username = (await message.bot.get_me()).username
            ref_link = f"https://t.me/{bot_username}?start=ref_{loyalty['ref_code']}"
            text += f"\n{E.LINK} <b>Реферальная ссылка:</b>\n"
            text += f"<code>{ref_link}</code>\n\n"
            text += f"{E.IDEA} Поделитесь ссылкой с друзьями!\n"
            text += f"За каждого нового пользователя вы получите {config.REFERRAL_BONUS} бонусов.\n\n"
    else:
        text += f"{E.STAR} <b>Программа лояльности:</b>\n"
        text += f"   • Визитов: 0\n"
        text += f"   • Бонусов: 0\n\n"

    if bookings:
        text += f"{E.LIST} <b>Активные записи:</b>\n\n"
        for b in bookings:
            date_str = keyboards._format_date(b['date'])
            text += f"• {date_str} в {b['time']}\n"
            text += f"  {E.BARBER} {b['service']}\n"
            text += f"  {E.CLOCK} {_booking_duration_text(b)}\n\n"

    await send_with_retry(message.bot, message.chat.id, text, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")


@router.message(Command("about"))
async def cmd_about(message: Message):
    """MED-01: Show information about the master."""
    text = messages.about_master_text()
    await send_with_retry(
        message.bot, message.chat.id,
        text,
        reply_markup=keyboards.back_to_main_kb(),
        parse_mode="HTML"
    )


@router.message(Command("contacts"))
async def cmd_contacts(message: Message):
    """MED-02: Show contacts and social links."""
    text = messages.CONTACTS.format(
        address=config.SALON_ADDRESS,
        phone=config.SALON_PHONE,
        hours=config.SALON_WORKING_HOURS,
    )
    await send_with_retry(
        message.bot, message.chat.id,
        text,
        reply_markup=keyboards.back_to_main_kb(),
        parse_mode="HTML"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    # FIX BUG-4: все обычные Unicode-эмодзи заменены на E.* (tg-emoji Premium)
    lines = [
        f"{E.BOOK} <b>Справка по командам:</b>",
        "",
        "<b>Основные команды:</b>",
        "• /start - Главное меню",
        "• /me - Информация о вас и ваших записях",
        "• /about - О мастере",
        "• /contacts - Контакты и соц.сети",
        "• /waitlist - Мои записи в листе ожидания",
        "• /cancel - Отменить запись (с указанием ID)",
        "• /help - Эта справка",
        f"<b>{E.IDEA} Как записаться:</b>",
        f"1. Нажмите «{E.SCISSORS} Записаться» в главном меню",
        "2. Выберите услугу",
        "3. Выберите дату и время",
        "4. Введите ваше имя",
        "5. Подтвердите запись",
        "",
        f"<b>{E.LIST} Мои записи:</b>",
        "• Просмотр всех активных записей",
        "• Подробная информация о каждой записи",
        "• Безопасная отмена с подтверждением",
        "",
        f"<b>{E.PHONE} Если возникли вопросы:</b>",
        f"Используйте кнопку «{E.PHONE} Контакты» в главном меню.",
    ]
    text = "\n".join(lines)
    await send_with_retry(message.bot, message.chat.id, text, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")


@router.message(Command("waitlist"))
async def cmd_waitlist(message: Message):
    """BUG-021: Show user's waitlist entries"""
    telegram_id = message.from_user.id
    
    try:
        # FIX: Use SQL query instead of Python filtering
        user_waitlist = await storage.get_user_waitlist(telegram_id)
        
        if not user_waitlist:
            await send_with_retry(
                message.bot, message.chat.id,
                f"{E.EMPTY} <b>У вас нет записей в листе ожидания</b>\n\n"
                f"Когда вы встанете в лист ожидания на занятое время, они появятся здесь.",
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML"
            )
            return
        
        text = f"{E.RELOAD} <b>Ваш лист ожидания:</b>\n\n"
        for i, w in enumerate(user_waitlist, 1):
            date_str = keyboards._format_date(w['date'])
            text += f"<b>{i}. {date_str} в {w['time']}</b>\n"
            text += f"   {E.MASTER} Нейл-мастер: {html.escape(w['master'])}\n"
            text += f"   {E.BARBER} Услуга: {html.escape(w['service'])}\n"
            text += f"   {E.CLOCK} Длительность: {_booking_duration_text(w)}\n\n"
        
        text += f"{E.INFO} Мы уведомим вас, когда время освободится!"
        
        await send_with_retry(
            message.bot, message.chat.id,
            text,
            reply_markup=keyboards.back_to_main_kb(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in cmd_waitlist: {e}")
        await send_with_retry(
            message.bot, message.chat.id,
            messages.ERROR,
            parse_mode="HTML"
        )




@router.callback_query(F.data == "invite_friend")
async def cb_invite_friend(callback: CallbackQuery):
    """REFERRAL: Персональная реферальная ссылка + статистика.
    ref_code создаётся для любого пользователя, даже если он ещё не посещал студию.
    E.* (premium tg-emoji) встроены в messages.INVITE_FRIEND напрямую; в кнопках emoji не используются.
    """
    telegram_id = callback.from_user.id
    first_name = callback.from_user.first_name or ""

    try:
        ref_code = await storage.ensure_user_ref_code(telegram_id, first_name)
        loyalty = await storage.get_loyalty(telegram_id)
        referral_count = await storage.get_referral_count(telegram_id)
        bonuses = loyalty["bonuses"] if loyalty else 0

        bot_info = await callback.bot.get_me()
        bot_username = bot_info.username
        ref_link = f"https://t.me/{bot_username}?start=ref_{ref_code}"

        # E.* встроены в сам шаблон INVITE_FRIEND — передаём только данные
        text = messages.INVITE_FRIEND.format(
            link=ref_link,
            bonus=config.REFERRAL_BONUS,
            count=referral_count,
            bonuses=bonuses,
        )

        # INVITE_FRIEND_SHARE — для switch_inline_query (почти plain text, не HTML)
        share_text = messages.INVITE_FRIEND_SHARE.format(
            name=html.escape(config.SALON_NAME),
            link=ref_link,
        )

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Поделиться ссылкой",
                switch_inline_query=share_text[:512],
            )],
            [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
        ])

        await edit_with_retry(
            callback.message, text,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in cb_invite_friend: {e}")
        await callback.answer("Ошибка. Попробуйте позже.", show_alert=True)
    await callback.answer()


@router.message(Command("cancel"))
async def cmd_cancel_universal(message: Message, state: FSMContext):
    """Universal cancel command - works in any state"""
    current_state = await state.get_state()

    # If user is in FSM state, clear it
    if current_state:
        # BUG-FIX: release slot_lock if user had a time slot selected
        data = await state.get_data()
        if data.get("date") and data.get("time") and data.get("master"):
            try:
                await storage.release_slot_lock(
                    data["date"], data["time"], data["master"],
                    duration_minutes=data.get("duration_minutes"),
                    owner_id=message.from_user.id,
                    owner_token=data.get("lock_owner_token"),
                )
            except Exception:
                pass
        await state.clear()
        await send_with_retry(
            message.bot, message.chat.id,
            f"{E.CHECK} <b>Действие отменено</b>\n\nВозвращаемся в главное меню.",
            reply_markup=keyboards.back_to_main_kb(),
            parse_mode="HTML"
        )
        return

    if not message.text:
        await send_with_retry(message.bot, message.chat.id, messages.NO_ACTIVE_BOOKING)
        return

    parts = message.text.split()
    if len(parts) > 1:
        # UX-FIX: /cancel BOOKING_ID now shows confirmation instead of instant delete
        booking_id = parts[1].strip()
        bookings = await storage.get_user_bookings(message.from_user.id)
        booking = next((b for b in bookings if b['id'] == booking_id), None)
        if not booking:
            await send_with_retry(
                message.bot, message.chat.id,
                f"{E.CROSS} <b>Запись не найдена</b>\n\nВозможно, она уже отменена.",
                reply_markup=keyboards.back_to_main_kb(),
                parse_mode="HTML"
            )
            return
        date_str = keyboards._format_date(booking['date'])
        confirm_text = (
            f"{E.WARNING} <b>Подтвердите отмену</b>\n\n"
            f"{E.CALENDAR} <b>Дата:</b> {date_str}\n"
            f"{E.CLOCK} <b>Время:</b> {booking['time']}\n"
            f"{E.MASTER} <b>Мастер:</b> {html.escape(booking['master'])}\n"
            f"{E.BARBER} <b>Услуга:</b> {html.escape(booking['service'])}\n\n"
            f"{E.IDEA} <b>Это действие нельзя отменить!</b>"
        )
        await send_with_retry(
            message.bot, message.chat.id,
            confirm_text,
            reply_markup=keyboards.confirm_cancel_kb(booking_id),
            parse_mode="HTML"
        )
        return

    # No ID given — show list with one-tap cancel buttons
    bookings = await storage.get_user_bookings(message.from_user.id)
    if not bookings:
        await send_with_retry(
            message.bot, message.chat.id,
            f"{E.INFO} <b>Нет активных записей</b>\n\nЗапишитесь через главное меню!",
            reply_markup=keyboards.back_to_main_kb(),
            parse_mode="HTML"
        )
        return

    text = f"{E.LIST} <b>Выберите запись для отмены:</b>\n\n"
    for b in bookings:
        date_str = keyboards._format_date(b['date'])
        text += f"{E.CALENDAR} <b>{date_str}</b> в {b['time']} — {html.escape(b['master'])}\n"

    await send_with_retry(
        message.bot, message.chat.id,
        text,
        reply_markup=keyboards.cancel_bookings_kb(bookings),
        parse_mode="HTML"
    )
