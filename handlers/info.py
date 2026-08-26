# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import html as html_lib
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InputMediaPhoto

import messages
import keyboards
import config
import storage
from utils import edit_with_retry
from emoji_config import E

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == "contacts")
async def cb_contacts(callback: CallbackQuery):
    try:
        text = f"{E.PHONE} <b>Связаться</b>\n\n"
        text += f"{E.PHONE} <b>Телефон:</b> {html_lib.escape(config.SALON_PHONE)}\n"
        text += f"{E.LOCATION} <b>Адрес:</b>\n{html_lib.escape(config.SALON_ADDRESS)}\n\n"
        text += f"{E.CLOCK} <b>Часы работы:</b>\n{html_lib.escape(config.SALON_WORKING_HOURS)}"

        links = await storage.get_social_links()
        kb = keyboards.back_to_main_kb()
        if links:
            rows = []
            for i in range(0, len(links), 2):
                row = []
                for link in links[i:i+2]:
                    row.append(InlineKeyboardButton(text=link["platform"], url=link["url"]))
                rows.append(row)
            rows.append([InlineKeyboardButton(text="Назад в меню", callback_data="main_menu")])
            from aiogram.types import InlineKeyboardMarkup
            kb = InlineKeyboardMarkup(inline_keyboard=rows)

        await edit_with_retry(callback.message, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in cb_contacts: {e}")
    await callback.answer()


@router.callback_query(F.data == "prices")
async def cb_prices(callback: CallbackQuery):
    try:
        text = f"{E.MONEY} <b>Услуги и цены:</b>\n\n"
        for name, price in config.SERVICES.items():
            text += f"• {name} — <b>{price:,} ₸</b>\n".replace(",", " ")
        await edit_with_retry(callback.message, text, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in cb_prices: {e}")
    await callback.answer()


@router.callback_query(F.data == "about_master")
async def cb_about_master(callback: CallbackQuery):
    try:
        text = messages.about_master_text()
        await edit_with_retry(callback.message, text, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in cb_about_master: {e}")
    await callback.answer()


@router.callback_query(F.data == "address")
async def cb_address(callback: CallbackQuery):
    try:
        text = (
            f"{E.LOCATION} <b>Адрес</b>\n\n"
            f"{html_lib.escape(config.SALON_ADDRESS)}\n\n"
            f"{E.CLOCK} <b>Часы работы:</b>\n{html_lib.escape(config.SALON_WORKING_HOURS)}"
        )
        await edit_with_retry(callback.message, text, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
        if config.SALON_LOCATION_LAT and config.SALON_LOCATION_LON:
            try:
                await callback.message.answer_location(
                    latitude=config.SALON_LOCATION_LAT,
                    longitude=config.SALON_LOCATION_LON
                )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Error in cb_address: {e}")
    await callback.answer()


# ===== ПОРТФОЛИО =====

@router.callback_query(F.data == "portfolio")
async def cb_portfolio(callback: CallbackQuery):
    try:
        photos = await storage.get_portfolio_photos(limit=1, offset=0)
        links = await storage.get_social_links()
        if not photos:
            text = messages.portfolio_empty_text()
            await edit_with_retry(callback.message, text, reply_markup=keyboards.back_to_main_kb(), parse_mode="HTML")
            await callback.answer()
            return

        photo = photos[0]
        total = await storage.count_portfolio_photos()
        text = messages.portfolio_caption(1, total, photo.get("caption", ""))
        await callback.message.edit_text(text, parse_mode="HTML")
        await callback.message.edit_media(
            media=InputMediaPhoto(media=photo["file_id"]),
            reply_markup=keyboards.portfolio_kb(1, has_prev=False, has_next=total > 1, links=links),
        )
    except Exception as e:
        logger.error(f"Error in cb_portfolio: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("portfolio_page:"))
async def cb_portfolio_page(callback: CallbackQuery):
    try:
        page = int(callback.data.split(":", 1)[1])
        total = await storage.count_portfolio_photos()
        if page < 1 or page > total:
            await callback.answer("Фото не найдено", show_alert=True)
            return

        photos = await storage.get_portfolio_photos(limit=1, offset=page - 1)
        if not photos:
            await callback.answer("Фото не найдено", show_alert=True)
            return

        links = await storage.get_social_links()
        photo = photos[0]
        text = messages.portfolio_caption(page, total, photo.get("caption", ""))
        await callback.message.edit_text(text, parse_mode="HTML")
        await callback.message.edit_media(
            media=InputMediaPhoto(media=photo["file_id"]),
            reply_markup=keyboards.portfolio_kb(
                page, has_prev=page > 1, has_next=page < total, links=links
            ),
        )
    except Exception as e:
        logger.error(f"Error in cb_portfolio_page: {e}")
    await callback.answer()
