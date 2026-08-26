# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import logging
import asyncio
import re
from typing import Optional
from aiogram.types import InlineKeyboardMarkup
from aiogram.exceptions import TelegramRetryAfter
import html as _html

logger = logging.getLogger(__name__)


async def send_with_retry(
    bot, 
    chat_id: int, 
    text: str, 
    reply_markup: Optional[InlineKeyboardMarkup] = None, 
    max_retries: int = 3, 
    retry_delay: float = 1.0,
    parse_mode: Optional[str] = "HTML"  # HTML по умолчанию
) -> bool:
    for attempt in range(max_retries):
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
            return True
        except TelegramRetryAfter as e:
            wait = max(e.retry_after + 1, retry_delay)
            logger.warning(f"Rate limited by Telegram, waiting {wait}s (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait)
            else:
                logger.error(f"Failed to send message to {chat_id} after {max_retries} attempts (last: rate limit)")
                return False
        except Exception as e:
            _es = str(e).lower()
            if any(s in _es for s in ("message is not modified", "bot was blocked", "user is deactivated", "chat not found", "forbidden")):
                logger.warning(f"Permanent error sending to {chat_id}, not retrying: {e}")
                return False
            logger.warning(f"Failed to send message to {chat_id} (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (2 ** attempt))  # exponential backoff
    logger.error(f"Failed to send message to {chat_id} after {max_retries} attempts")
    return False


async def edit_with_retry(
    message, 
    text: str, 
    reply_markup: Optional[InlineKeyboardMarkup] = None, 
    max_retries: int = 3, 
    retry_delay: float = 1.0,
    parse_mode: Optional[str] = "HTML"  # HTML по умолчанию
) -> bool:
    for attempt in range(max_retries):
        try:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return True
        except TelegramRetryAfter as e:
            wait = max(e.retry_after + 1, retry_delay)
            logger.warning(f"Rate limited by Telegram while editing, waiting {wait}s (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait)
            else:
                logger.error(f"Failed to edit message after {max_retries} attempts (last: rate limit)")
                return False
        except Exception as e:
            _es = str(e).lower()
            if any(s in _es for s in ("message is not modified", "bot was blocked", "user is deactivated", "chat not found", "forbidden")):
                logger.warning(f"Permanent error editing message, not retrying: {e}")
                return False
            logger.warning(f"Failed to edit message (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (2 ** attempt))  # exponential backoff
    logger.error(f"Failed to edit message after {max_retries} attempts")
    return False


async def notify_admins(bot, text: str, parse_mode: str = "HTML") -> None:
    """REFACTOR: Centralised admin notification helper.
    Replaces scattered for admin_id in config.ADMIN_IDS loops.
    """
    import config
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=parse_mode)
        except Exception as _e:
            _es = str(_e).lower()
            if "retry after" in _es:
                try:
                    import re as _re
                    match = _re.search(r"retry after (\d+)", _es)
                    wait = int(match.group(1)) + 1 if match else 5
                    logger.warning(f"Rate limited notifying admin {admin_id}, waiting {wait}s")
                    await asyncio.sleep(wait)
                    await bot.send_message(admin_id, text, parse_mode=parse_mode)
                except Exception as _e2:
                    logger.error(f"Failed to notify admin {admin_id} after retry: {_e2}")
            else:
                logger.error(f"Failed to notify admin {admin_id}: {_e}")


def sanitize_text(text: str, max_length: int = 500) -> str:
    """Sanitize user input: escape HTML, strip excessive whitespace, limit length."""
    if not text:
        return ""
    text = text.strip()
    text = _html.escape(text, quote=True)
    if len(text) > max_length:
        text = text[:max_length] + "…"
    return text


def validate_name(name: str) -> tuple[bool, str]:
    """Validate a user name. Returns (is_valid, error_message)."""
    if not name or not name.strip():
        return False, "Имя не может быть пустым."
    name = name.strip()
    if len(name) > 50:
        return False, "Имя должно содержать не более 50 символов."
    if not re.match(r'^[a-zA-Zа-яА-ЯёЁ\s\-\']+$', name):
        return False, "Имя может содержать только буквы, пробелы, дефисы и апострофы."
    if not re.search(r'[a-zA-Zа-яА-ЯёЁ]', name):
        return False, "Имя должно содержать хотя бы одну букву."
    return True, ""


def validate_phone(raw_phone: str) -> tuple[bool, str]:
    """Validate a phone number. Returns (is_valid, normalized_or_error)."""
    import config
    if not raw_phone:
        return False, "Номер телефона не может быть пустым."
    cleaned = re.sub(r"[\s\-\(\)]", "", raw_phone.strip())
    country_code = config.PHONE_COUNTRY_CODE
    national_digits = config.PHONE_NATIONAL_DIGITS

    if country_code == "7" and cleaned.startswith("8") and len(cleaned) == national_digits + 1:
        cleaned = "7" + cleaned[1:]
    elif not cleaned.startswith("+") and len(cleaned) == national_digits:
        cleaned = country_code + cleaned

    if cleaned.startswith("+"):
        digits = cleaned[1:]
    else:
        digits = cleaned

    expected_len = len(country_code) + national_digits
    if not digits.isdigit() or not digits.startswith(country_code) or len(digits) != expected_len:
        return False, "Неверный формат номера. Введите в формате +7 700 123 45 67"
    if len(digits) > 15:
        return False, "Номер слишком длинный."
    return True, f"+{digits}"


def validate_comment(comment: str) -> tuple[bool, str]:
    """Validate a review comment. Returns (is_valid, sanitized_or_error)."""
    if not comment or not comment.strip():
        return True, ""  # empty is valid
    comment = comment.strip()
    if len(comment) > 500:
        return False, "Комментарий слишком длинный. Максимум 500 символов."
    return True, _html.escape(comment)
