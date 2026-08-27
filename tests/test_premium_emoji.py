import ast
from pathlib import Path
from emoji_config import EMOJI_IDS, E, P, icon_button, tg
import keyboards


def test_icon_button_serializes_custom_id():
    b = icon_button("Test", "settings", callback_data="x")
    assert b.icon_custom_emoji_id == EMOJI_IDS["settings"]


def test_keyboard_factory_buttons_are_plain_and_allowlisted():
    cases = [keyboards.main_menu_kb(), keyboards.back_to_main_kb(), keyboards.phone_kb(),
             keyboards.confirm_cancel_kb("1")]
    for markup in cases:
        for row in markup.inline_keyboard if hasattr(markup, "inline_keyboard") else markup.keyboard:
            for button in row:
                assert not any(ord(c) > 0x1F000 for c in button.text)
                if hasattr(button, "icon_custom_emoji_id") and button.icon_custom_emoji_id:
                    assert button.icon_custom_emoji_id in EMOJI_IDS.values()


def test_representative_messages_use_tg_emoji():
    assert E.CHECK.startswith('<tg-emoji emoji-id=')
    assert P.CHECK == "✅"
    assert 'emoji-id="' in tg("settings", "⚙️")


def test_messages_tags_are_html_compatible():
    import messages
    assert "<b>" in messages.MAIN_MENU_TEXT
    assert '<tg-emoji' in messages.MAIN_MENU_TEXT


def test_registered_sources_have_no_literal_emoji_except_alerts():
    paths = [Path("messages.py"), *(Path("handlers") / name for name in (
        "start.py", "demo.py", "demo_booking.py", "info.py", "admin.py", "booking.py"
    ))]
    # source-level smoke check: migration must centralize ordinary message icons
    for path in paths:
        if path.exists():
            tree = ast.parse(path.read_text(encoding="utf8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value.startswith("<tg-emoji"):
                        continue
                    # Callback alerts may retain plain glyphs; ordinary UI text must not.
                    if path.name == "booking.py" and node.lineno in {880, 910, 1054, 1059, 1065, 1070, 132}:
                        continue
                    assert not any(("\U0001F300" <= c <= "\U0001FAFF") or ("\u2600" <= c <= "\u27BF") for c in node.value)


def test_handlers_use_central_button_factory():
    for path in (Path("handlers") / name for name in (
        "start.py", "demo.py", "demo_booking.py", "info.py", "admin.py", "booking.py"
    )):
        tree = ast.parse(path.read_text(encoding="utf8"))
        assert not any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"InlineKeyboardButton", "KeyboardButton"}
            for node in ast.walk(tree)
        )
