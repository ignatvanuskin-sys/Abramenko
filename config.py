# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import os
from urllib.parse import urlparse
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
import logging as _logging
_cfg_logger = _logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _cfg_logger.warning(f"config: Invalid integer for {name}: {raw}")
        return default
    if minimum is not None and value < minimum:
        _cfg_logger.warning(f"config: {name} must be >= {minimum}, got {value}")
        return default
    return value


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        _cfg_logger.warning(f"config: Invalid float for {name}: {raw}")
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    pass


def is_production() -> bool:
    return APP_ENV in {"prod", "production"}


def validate_webhook_url(url: str | None = None) -> str:
    value = (WEBHOOK_URL if url is None else url).strip().rstrip("/")
    if not value:
        raise ConfigError("WEBHOOK_URL is required when BOT_MODE=webhook")
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ConfigError("WEBHOOK_URL must start with https://")
    if not parsed.netloc or "." not in parsed.netloc:
        raise ConfigError("WEBHOOK_URL must contain a public hostname")
    if parsed.query or parsed.fragment:
        raise ConfigError("WEBHOOK_URL must not contain query string or fragment")
    return value


def validate_webhook_config() -> bool:
    validate_webhook_url(WEBHOOK_URL)
    if not WEBHOOK_PATH.strip():
        raise ConfigError("WEBHOOK_PATH must not be empty")
    if is_production() and not WEBHOOK_SECRET_TOKEN:
        raise ConfigError("SECRET_TOKEN is required in production webhook mode")
    return True


def validate_runtime_config() -> bool:
    if BOT_MODE not in {"polling", "webhook"}:
        raise ConfigError("BOT_MODE must be 'polling' or 'webhook'")
    if INVALID_ADMIN_IDS:
        raise ConfigError(f"ADMIN_CHAT_ID must be an integer, got: {', '.join(INVALID_ADMIN_IDS)}")
    if BOT_MODE == "webhook":
        return validate_webhook_config()
    return True


ADMIN_IDS = []
INVALID_ADMIN_IDS = []
for _raw_id in os.getenv("ADMIN_IDS", os.getenv("ADMIN_CHAT_ID", "")).split(","):
    _raw_id = _raw_id.strip()
    if _raw_id:
        try:
            ADMIN_IDS.append(int(_raw_id))
        except ValueError:
            INVALID_ADMIN_IDS.append(_raw_id)
            _cfg_logger.warning(f"config: Invalid ADMIN_ID: {_raw_id}")

# Demo delivery intentionally has one durable recipient contract.  This avoids
# duplicate sends when a partially successful multi-admin attempt is retried.
DEMO_ADMIN_CHAT_ID = ADMIN_IDS[0] if ADMIN_IDS else None
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH = os.getenv("DB_PATH", "./data/nailshop.db")
# Explicitly bounded broadcast fan-out; keep this defined for every runtime.
MAX_BROADCAST_RECIPIENTS = _env_int("MAX_BROADCAST_RECIPIENTS", 1000, minimum=1)
TIMEZONE = os.getenv("TIMEZONE", "Asia/Almaty")

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
BOT_MODE = os.getenv("BOT_MODE", "polling").strip().lower()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook").strip() or "/webhook"
WEBHOOK_SECRET_TOKEN = os.getenv("SECRET_TOKEN", "").strip()
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = _env_int("PORT", _env_int("WEBHOOK_PORT", 8080, minimum=1), minimum=1)
POLLING_CONFLICT_RETRIES = _env_int("POLLING_CONFLICT_RETRIES", 24, minimum=1)
POLLING_CONFLICT_RETRY_DELAY = _env_float("POLLING_CONFLICT_RETRY_DELAY", 5.0)
REQUIRE_REDIS_FSM = _env_bool("REQUIRE_REDIS_FSM", APP_ENV in {"prod", "production"})

# Основные плейсхолдеры бренда. Изменяйте здесь или через админ-панель бота.
SALON_NAME = os.getenv("SALON_NAME", "Abramenko Studio")
MASTER_NAME = "Администратор Abramenko Studio"
MASTER_KEY = os.getenv("MASTER_KEY", "default").strip() or "default"
MASTER_DESCRIPTION = "Информацию о специалистах уточнит администратор."
MASTER_EXPERIENCE = "Точный опыт уточнит администратор"

# Константы лояльности
LOYALTY_VISIT_INTERVAL = _env_int("LOYALTY_VISIT_INTERVAL", 5, minimum=1)
LOYALTY_DISCOUNT_PERCENT = _env_int("LOYALTY_DISCOUNT_PERCENT", 10, minimum=0)
REFERRAL_BONUS = _env_int("REFERRAL_BONUS", 100, minimum=0)
SALON_ADDRESS = "Точный адрес уточнит администратор"
SALON_PHONE = "Точный телефон уточнит администратор"
SALON_INSTAGRAM = ""
SALON_TELEGRAM = ""
SALON_WORKING_HOURS = "Точный график уточнит администратор"
SALON_LOCATION_LAT = 0.0
SALON_LOCATION_LON = 0.0

def _make_services_copy() -> dict:
    """Thread-safe copy of default services to prevent mutation from concurrent access."""
    return {
        "Женская стрижка": 0,
        "Мужская стрижка": 0,
        "Детская стрижка": 0,
        "Окрашивание": 0,
        "Тонирование": 0,
        "Уход": 0,
    }

SERVICES = _make_services_copy()

SLOT_STEP_MINUTES = 30
DEFAULT_SERVICE_DURATION_MINUTES = 30
def _make_durations_copy() -> dict:
    """Thread-safe copy of default durations."""
    return {name: DEFAULT_SERVICE_DURATION_MINUTES for name in SERVICES}

SERVICE_DURATIONS = _make_durations_copy()


def get_service_duration(service_name: str) -> int:
    try:
        duration = int(SERVICE_DURATIONS.get(service_name, DEFAULT_SERVICE_DURATION_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_SERVICE_DURATION_MINUTES
    return duration if duration > 0 else DEFAULT_SERVICE_DURATION_MINUTES

WORKING_HOURS = {
    "monday":    (10, 21), "tuesday":   (10, 21),
    "wednesday": (10, 21), "thursday":  (10, 21),
    "friday":    (10, 21), "saturday":  (10, 21),
    "sunday":    (11, 19),
}

TIME_SLOTS = [
    "10:00","10:30","11:00","11:30","12:00","12:30","13:00","13:30",
    "14:00","14:30","15:00","15:30","16:00","16:30","17:00","17:30",
    "18:00","18:30","19:00","19:30","20:00","20:30",
]

MAX_BOOKING_ATTEMPTS = _env_int("MAX_BOOKING_ATTEMPTS", 10, minimum=1)
MAX_ACTIVE_BOOKINGS = _env_int("MAX_ACTIVE_BOOKINGS", 3, minimum=1)
MIN_BOOKING_ADVANCE_MINUTES = _env_int("MIN_BOOKING_ADVANCE_MINUTES", 60, minimum=0)
RATE_LIMIT_WINDOW = _env_int("RATE_LIMIT_WINDOW", 1800, minimum=1)
PHONE_COUNTRY_CODE = os.getenv("PHONE_COUNTRY_CODE", "7").strip().lstrip("+") or "7"
PHONE_NATIONAL_DIGITS = _env_int("PHONE_NATIONAL_DIGITS", 10, minimum=1)
MAX_PORTFOLIO_PHOTOS = _env_int("MAX_PORTFOLIO_PHOTOS", 60, minimum=1)
MAX_PORTFOLIO_PHOTO_SIZE_BYTES = _env_int("MAX_PORTFOLIO_PHOTO_SIZE_BYTES", 10 * 1024 * 1024, minimum=1)
DAILY_DIGEST_HOUR = _env_int("DAILY_DIGEST_HOUR", 21, minimum=0)
DAILY_DIGEST_MINUTE = _env_int("DAILY_DIGEST_MINUTE", 0, minimum=0)
SCHEDULER_LOCK_TTL_SECONDS = _env_int("SCHEDULER_LOCK_TTL_SECONDS", 120, minimum=1)

S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "").strip()
S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "").strip()
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "").strip()
S3_REGION = os.getenv("S3_REGION", "us-east-1").strip() or "us-east-1"
S3_BACKUP_PREFIX = os.getenv("S3_BACKUP_PREFIX", "backups").strip().strip("/")
PRIVACY_RETENTION_DAYS = _env_int("PRIVACY_RETENTION_DAYS", 365, minimum=1)
ADMIN_AUDIT_RETENTION_DAYS = _env_int("ADMIN_AUDIT_RETENTION_DAYS", 365, minimum=1)


async def load_config_from_db():
    global SALON_NAME, SALON_ADDRESS, SALON_PHONE, SALON_WORKING_HOURS
    global TIME_SLOTS, SERVICES, SERVICE_DURATIONS, WORKING_HOURS
    global MASTER_NAME, MASTER_DESCRIPTION, MASTER_EXPERIENCE
    global SALON_INSTAGRAM, SALON_TELEGRAM
    global SALON_LOCATION_LAT, SALON_LOCATION_LON
    try:
        from storage import get_all_service_durations, get_all_settings, get_all_services
        settings = await get_all_settings()
        if "salon_name" in settings: SALON_NAME = settings["salon_name"]
        if "address" in settings: SALON_ADDRESS = settings["address"]
        if "phone" in settings: SALON_PHONE = settings["phone"]
        if "hours" in settings: SALON_WORKING_HOURS = settings["hours"]
        if "slots" in settings:
            TIME_SLOTS = [s.strip() for s in settings["slots"].split(",")]
        if "working_hours_json" in settings:
            import json
            try: WORKING_HOURS = json.loads(settings["working_hours_json"])
            except Exception: pass
        if "master_name" in settings: MASTER_NAME = settings["master_name"]
        if "master_description" in settings: MASTER_DESCRIPTION = settings["master_description"]
        if "master_experience" in settings: MASTER_EXPERIENCE = settings["master_experience"]
        if "instagram" in settings: SALON_INSTAGRAM = settings["instagram"]
        if "telegram_contact" in settings: SALON_TELEGRAM = settings["telegram_contact"]
        if "location_lat" in settings:
            try: SALON_LOCATION_LAT = float(settings["location_lat"])
            except Exception: pass
        if "location_lon" in settings:
            try: SALON_LOCATION_LON = float(settings["location_lon"])
            except Exception: pass
        services = await get_all_services()
        if services:
            # Thread-safe: replace entire dict atomically
            new_services = {}
            new_services.update(services)
            SERVICES.clear()
            SERVICES.update(new_services)
        durations = await get_all_service_durations()
        if durations:
            new_durations = {}
            new_durations.update(durations)
            SERVICE_DURATIONS.clear()
            SERVICE_DURATIONS.update(new_durations)
    except Exception as _e:
        _cfg_logger.error(f"Failed to load config from DB: {_e}", exc_info=True)


async def save_config_to_db():
    try:
        from storage import save_settings, save_service
        import json
        await save_settings("salon_name", SALON_NAME)
        await save_settings("address", SALON_ADDRESS)
        await save_settings("phone", SALON_PHONE)
        await save_settings("hours", SALON_WORKING_HOURS)
        await save_settings("slots", ",".join(TIME_SLOTS))
        await save_settings("working_hours_json", json.dumps(WORKING_HOURS))
        await save_settings("master_name", MASTER_NAME)
        await save_settings("master_description", MASTER_DESCRIPTION)
        await save_settings("master_experience", MASTER_EXPERIENCE)
        await save_settings("instagram", SALON_INSTAGRAM)
        await save_settings("telegram_contact", SALON_TELEGRAM)
        await save_settings("location_lat", str(SALON_LOCATION_LAT))
        await save_settings("location_lon", str(SALON_LOCATION_LON))
        for name, price in SERVICES.items():
            await save_service(name, price, get_service_duration(name))
    except Exception as _e:
        _cfg_logger.error(f"Failed to save config to DB: {_e}", exc_info=True)
