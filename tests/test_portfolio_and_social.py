import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock

import storage
import keyboards
import config


class TestPortfolioStorage:

    async def test_add_and_get_portfolio_photo(self, db):
        photo_id = await storage.add_portfolio_photo("file_id_123", "My caption")
        assert photo_id is not None
        photo = await storage.get_portfolio_photo(photo_id)
        assert photo["file_id"] == "file_id_123"
        assert photo["caption"] == "My caption"

    async def test_get_portfolio_photos_pagination(self, db):
        ids = []
        for i in range(5):
            pid = await storage.add_portfolio_photo(f"file_id_{i}", f"cap {i}")
            ids.append(pid)
        photos = await storage.get_portfolio_photos(limit=2, offset=0)
        assert len(photos) == 2
        count = await storage.count_portfolio_photos()
        assert count == 5

    async def test_delete_portfolio_photo(self, db):
        photo_id = await storage.add_portfolio_photo("to_delete", "")
        deleted = await storage.delete_portfolio_photo(photo_id)
        assert deleted is True
        photo = await storage.get_portfolio_photo(photo_id)
        assert photo is None

    async def test_delete_portfolio_photo_not_found(self, db):
        deleted = await storage.delete_portfolio_photo(9999)
        assert deleted is False


class TestSocialLinksStorage:

    async def test_add_and_get_social_links(self, db):
        await storage.add_social_link("Instagram", "https://instagram.com/nail")
        await storage.add_social_link("TikTok", "https://tiktok.com/@nail")
        links = await storage.get_social_links()
        assert len(links) == 2
        platforms = {l["platform"] for l in links}
        assert "Instagram" in platforms
        assert "TikTok" in platforms

    async def test_delete_social_link(self, db):
        link_id = await storage.add_social_link("WhatsApp", "https://wa.me/123")
        deleted = await storage.delete_social_link(link_id)
        assert deleted is True
        links = await storage.get_social_links()
        assert all(l["platform"] != "WhatsApp" for l in links)

    async def test_get_social_link_by_id(self, db):
        link_id = await storage.add_social_link("Telegram", "https://t.me/nail")
        link = await storage.get_social_link_by_id(link_id)
        assert link["platform"] == "Telegram"
        assert link["url"] == "https://t.me/nail"


class TestBookingSafetyStorage:

    async def test_create_slot_lock_is_exclusive(self, db):
        first = await storage.create_slot_lock("2026-12-07", "10:00", "Anna")
        second = await storage.create_slot_lock("2026-12-07", "10:00", "Anna")

        assert first is True
        assert second is False

    async def test_save_booking_rejects_duplicate_active_slot(self, db):
        booking = {
            "date": "2026-12-07",
            "time": "10:00",
            "name": "Client One",
            "telegram_id": 101,
            "username": "one",
            "master": "Anna",
            "service": "Маникюр",
            "price": 3000,
        }

        first_id = await storage.save_booking(booking)
        second_id = await storage.save_booking({**booking, "name": "Client Two", "telegram_id": 202})

        assert first_id
        assert second_id is None
        booked = await storage.get_booked_slots("2026-12-07", "Anna")
        assert [row["time"] for row in booked] == ["10:00"]


class TestUserBlockingAndAudit:

    async def test_user_block_flag_roundtrip(self, db):
        await storage.save_user(555, phone="+77001234567", username="u", first_name="Test")

        assert await storage.is_user_blocked(555) is False
        await storage.set_user_blocked(555, True)
        assert await storage.is_user_blocked(555) is True

        users = await storage.get_all_users()
        assert users[0]["telegram_id"] == 555
        assert users[0]["blocked"] == 1

    async def test_admin_audit_log_roundtrip(self, db):
        await storage.log_admin_action(
            admin_id=1,
            action="service_add",
            entity_type="service",
            entity_id="Маникюр",
            old_value="",
            new_value="3000",
        )

        rows = await storage.get_admin_audit_log(limit=10)
        assert len(rows) == 1
        assert rows[0]["admin_id"] == 1
        assert rows[0]["action"] == "service_add"
        assert rows[0]["entity_type"] == "service"
        assert rows[0]["entity_id"] == "Маникюр"
        assert rows[0]["new_value"] == "3000"


class TestPortfolioKeyboards:

    def test_portfolio_kb_navigation(self):
        kb = keyboards.portfolio_kb(
            photo_id=2, has_prev=True, has_next=True, links=[]
        )
        assert len(kb.inline_keyboard) >= 2  # nav + back
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert "◀ Назад" in texts
        assert "Далее ▶" in texts
        assert "Назад в меню" in texts

    def test_portfolio_kb_with_social_links(self):
        links = [
            {"platform": "Instagram", "url": "https://instagram.com/nail"},
            {"platform": "TikTok", "url": "https://tiktok.com/@nail"},
        ]
        kb = keyboards.portfolio_kb(
            photo_id=1, has_prev=False, has_next=True, links=links
        )
        # Social links should be URL buttons
        url_buttons = [b for row in kb.inline_keyboard for b in row if b.url]
        assert len(url_buttons) == 2

    def test_portfolio_admin_kb(self):
        kb = keyboards.portfolio_admin_kb()
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert "Добавить фото" in texts
        assert "Удалить фото" in texts
        assert "Назад" in texts

    def test_social_links_admin_kb(self):
        links = [{"id": 1, "platform": "Instagram", "url": "https://instagram.com/nail"}]
        kb = keyboards.social_links_admin_kb(links)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Instagram" in t for t in texts)
        assert "Добавить ссылку" in texts

    def test_confirm_delete_photo_kb(self):
        kb = keyboards.confirm_delete_photo_kb(photo_id=5)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert "Да, удалить" in texts
        assert "Отмена" in texts

    def test_admin_booking_kb_has_block_action(self):
        kb = keyboards.admin_cancel_booking_kb("booking1", telegram_id=555, user_blocked=False)
        buttons = [b for row in kb.inline_keyboard for b in row]

        assert any(b.text == "Заблокировать клиента" for b in buttons)
        assert any(b.callback_data == "admin_user_block:555:block:booking1" for b in buttons)

    def test_admin_booking_kb_has_unblock_action(self):
        kb = keyboards.admin_cancel_booking_kb("booking1", telegram_id=555, user_blocked=True)
        buttons = [b for row in kb.inline_keyboard for b in row]

        assert any(b.text == "Разблокировать клиента" for b in buttons)
        assert any(b.callback_data == "admin_user_block:555:unblock:booking1" for b in buttons)


class TestPhoneNormalization:

    def test_kazakhstan_phone_normalized_to_e164(self):
        from utils import validate_phone

        assert validate_phone("+7 700 123 45 67") == (True, "+77001234567")
        assert validate_phone("87001234567") == (True, "+77001234567")
        assert validate_phone("7001234567") == (True, "+77001234567")

    def test_invalid_phone_rejected(self):
        from utils import validate_phone

        assert validate_phone("+7 700 123")[0] is False
        assert validate_phone("+1 700 123 45 67")[0] is False
        assert validate_phone("not-a-phone")[0] is False
