# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Central Telegram Premium/custom emoji configuration."""

from aiogram.types import InlineKeyboardButton, KeyboardButton

# Authoritative IDs supplied for this bot.  Keep IDs in this module only.
EMOJI_IDS = {
    "settings": "5870982283724328568", "profile": "5870994129244131212",
    "people": "5870772616305839506", "person_check": "5891207662678317861",
    "person_cross": "5893192487324880883", "file": "5870528606328852614",
    "smile": "5870764288364252592", "chart_growth": "5870930636742595124",
    "chart_stats": "5870921681735781843", "home": "5873147866364514353",
    "lock": "6037249452824072506", "unlock": "6037496202990194718",
    "megaphone": "6039422865189638057", "check": "5870633910337015697",
    "cross": "5870657884844462243", "pencil": "5870676941614354370",
    "trash": "5870875489362513438", "down": "5893057118545646106",
    "attachment": "6039451237743595514", "link": "5769289093221454192",
    "info": "6028435952299413210", "phone": "6030400221232501136", "bot": "6030400221232501136",
    "eye": "6037397706505195857", "hidden": "6037243349675544634",
    "send": "5963103826075456248", "download": "6039802767931871481",
    "bell": "6039486778597970865", "gift": "6032644646587338669",
    "clock": "5983150113483134607", "celebration": "6041731551845159060",
    "font_link": "5870801517140775623", "write": "5870753782874246579",
    "media": "6035128606563241721", "location": "6042011682497106307",
    "wallet": "5769126056262898415", "box": "5884479287171485878",
    "cryptobot": "5260752406890711732", "calendar": "5890937706803894250",
    "tag": "5886285355279193209", "elapsed": "5775896410780079073",
    "apps": "5778672437122045013", "brush": "6050679691004612757",
    "add_text": "5771851822897566479", "format": "5778479949572738874",
    "money": "5904462880941545555", "send_money": "5890848474563352982",
    "receive_money": "5879814368572478751", "code": "5940433880585605708",
    "loading": "5345906554510012647",
}

# Unicode aliases are retained only as an ergonomic input API.
ALIASES = {
    "⚙️": "settings", "👤": "profile", "👥": "people", "✅": "check",
    "❌": "cross", "📄": "file", "📈": "chart_growth", "📊": "chart_stats",
    "🏠": "home", "🔒": "lock", "🔓": "unlock", "📣": "megaphone",
    "✏️": "pencil", "🗑️": "trash", "⬇️": "down", "📎": "attachment",
    "🔗": "link", "ℹ️": "info", "🤖": "bot", "👁️": "eye", "🙈": "hidden",
    "📤": "send", "📥": "download", "🔔": "bell", "🎁": "gift", "🕒": "clock",
    "🎉": "celebration", "🔤": "font_link", "✍️": "write", "🖼️": "media",
    "📍": "location", "💳": "wallet", "📦": "box", "📅": "calendar",
    "🏷️": "tag", "⏱️": "elapsed", "📱": "apps", "🎨": "brush", "➕": "add_text",
    "💰": "money", "💸": "send_money", "💵": "receive_money", "</>": "code",
    "⏳": "loading", "🙂": "smile",
}


def tg(name: str, fallback: str = "") -> str:
    """Return Telegram HTML custom emoji; unknown names fail closed."""
    emoji_id = EMOJI_IDS[name]
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def reply_icon_button(text: str, icon: str | None = None, **kwargs):
    """Build a reply button, retaining custom-emoji fields when supported."""
    data = {"text": text, **kwargs}
    if icon:
        data["icon_custom_emoji_id"] = EMOJI_IDS[icon]
    return KeyboardButton.model_validate(data)


def icon_button(text: str, icon: str | None = None, **kwargs):
    """Build a button retaining Bot API custom-emoji fields on old aiogram."""
    if not icon:
        return InlineKeyboardButton(text=text, **kwargs)
    data = {"text": text, **kwargs, "icon_custom_emoji_id": EMOJI_IDS[icon]}
    # aiogram 3.7 rejects this newer Bot API field in its constructor, while
    # model_validate preserves extra fields for serialization.
    return InlineKeyboardButton.model_validate(data)


FALLBACKS = {
    "settings": "⚙️", "profile": "👤", "people": "👥", "file": "📁",
    "smile": "🙂", "chart_growth": "📊", "chart_stats": "📊", "home": "🏘",
    "lock": "🔒", "unlock": "🔓", "megaphone": "📣", "check": "✅",
    "cross": "❌", "pencil": "🖋", "trash": "🗑", "down": "📰",
    "attachment": "📎", "link": "🔗", "info": "ℹ️", "bot": "🤖",
    "phone": "🤖", "eye": "👁", "hidden": "👁", "send": "⬆", "download": "⬇",
    "bell": "🔔", "gift": "🎁", "clock": "⏰", "celebration": "🎉",
    "font_link": "🔗", "write": "✍", "media": "🖼", "location": "📍",
    "wallet": "👛", "box": "📦", "cryptobot": "👾", "calendar": "📅",
    "tag": "🏷", "elapsed": "🕓", "apps": "📦", "brush": "🖌",
    "add_text": "🔡", "format": "↔", "money": "🪙", "send_money": "🪙",
    "receive_money": "🏧", "code": "🔨", "loading": "🔄",
    "person_check": "👤", "person_cross": "👤",
}


def emoji(value: str, fallback: bool = True) -> str:
    name = ALIASES.get(value)
    if name:
        return tg(name, value)
    return value if fallback else ""


class _EmojiNamespace:
    def __getattr__(self, name):
        key = name.lower()
        legacy = {"scissors": "brush", "barber": "home", "master": "profile", "user": "profile", "houses": "home", "calendar_alt": "calendar", "clock_9": "clock", "timer": "elapsed", "exclamation": "info", "warning": "info", "check_small": "check", "cross_small": "cross", "list": "file", "note": "write", "chart": "chart_stats", "peach": "smile", "target": "apps", "clap": "celebration", "star": "smile", "idea": "info", "book": "file", "id": "info", "number": "apps", "comment": "info", "empty": "box", "plus": "add_text", "reload": "loading", "camera": "media", "search": "eye", "folder": "file", "artist": "brush", "artist_woman": "brush", "palette": "brush", "plane": "send", "writing": "write", "lightning": "loading", "hand_stop": "cross", "point_down": "down", "arrow_down": "down", "label": "tag", "woman": "profile"}
        semantic = key if key in EMOJI_IDS else legacy.get(key, "info")
        return tg(semantic, FALLBACKS[semantic])


E = _EmojiNamespace()


class P:
    """Plain fallback glyphs for callback alerts (HTML is unsupported there)."""
    CHECK, CROSS, WARNING, INFO, EMPTY = "✅", "❌", "⚠️", "ℹ️", "📦"


CUSTOM_EMOJIS = {alias: EMOJI_IDS[name] for alias, name in ALIASES.items()}


def check_emoji_config() -> dict:
    mapping = CUSTOM_EMOJIS
    total = len(mapping)
    configured = sum(1 for value in mapping.values() if value)
    return {"total": total, "configured": configured, "missing": total - configured, "percent": round(configured / total * 100, 1) if total else 0}
