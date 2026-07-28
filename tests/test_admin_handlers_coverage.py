from unittest.mock import AsyncMock, MagicMock, patch

from helpers import SAMPLE_BOOKING, make_callback, make_fsm, make_message


def _admin(monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [999])


async def test_admin_stats_audit_unavailable_flows(db, monkeypatch):
    from handlers import admin

    _admin(monkeypatch)
    cb = make_callback("admin_stats", user_id=999)
    await admin.cb_admin_stats(cb)
    assert cb.answer.await_count >= 1

    await admin._audit(999, "test_action", "entity", "1")
    cb = make_callback("admin_audit", user_id=999)
    await admin.cb_admin_audit(cb)
    cb.message.edit_text.assert_awaited()

    cb = make_callback("admin_unavailable", user_id=999)
    await admin.cb_admin_unavailable(cb)
    cb.message.edit_text.assert_awaited()

    state = make_fsm()
    cb = make_callback("admin_add_unavailable", user_id=999)
    await admin.cb_admin_add_unavailable(cb, state)
    state.set_state.assert_awaited_once()

    msg = make_message("2026-12-07, 10:00, 12:00, ремонт", user_id=999)
    await admin.handle_add_unavailable_period(msg, state)
    msg.bot.send_message.assert_awaited()

    cb = make_callback("admin_delete_unavailable:1", user_id=999)
    await admin.cb_admin_delete_unavailable(cb)
    cb.answer.assert_awaited()


async def test_admin_booking_manage_cancel_complete_flows(db, monkeypatch):
    import storage
    from handlers import admin

    _admin(monkeypatch)
    await storage.save_user(111, phone="+77770000000", username="u", first_name="Client")
    first_id = await storage.save_booking({**SAMPLE_BOOKING, "telegram_id": 111, "time": "10:00"})
    second_id = await storage.save_booking({**SAMPLE_BOOKING, "telegram_id": 112, "time": "12:00", "name": "Second"})
    await storage.add_to_waitlist(222, "Wait", "Alibek", "Haircut", SAMPLE_BOOKING["date"], "10:00")

    cb = make_callback("admin_bookings", user_id=999)
    await admin.cb_admin_bookings(cb)
    cb.message.edit_text.assert_awaited()

    cb = make_callback(f"admin_manage_booking:{first_id}", user_id=999)
    await admin.cb_admin_manage_booking(cb)
    cb.message.edit_text.assert_awaited()

    cb = make_callback(f"admin_user_block:111:block:{first_id}", user_id=999)
    await admin.cb_admin_user_block(cb)
    assert await storage.is_user_blocked(111) is True
    # HIGH-04 fix: blocking user 111 auto-cancelled first_id
    cancelled = await storage.get_booking_with_user(first_id)
    assert cancelled is None or cancelled["status"] == "cancelled"

    # second_id (user 112, not blocked) is still active
    cb = make_callback(f"admin_pre_cancel:{second_id}", user_id=999)
    await admin.cb_admin_pre_cancel(cb)
    cb.message.edit_text.assert_awaited()

    # Complete before cancel — booking must be active
    cb = make_callback(f"admin_complete_booking:{second_id}", user_id=999)
    await admin.cb_admin_complete_booking(cb)
    cb.message.edit_text.assert_awaited()

    # Create a new booking for the cancel flow (second_id was completed above)
    third_id = await storage.save_booking({**SAMPLE_BOOKING, "telegram_id": 112, "time": "14:00", "name": "Third"})
    bot = AsyncMock()
    cb = make_callback(f"admin_pre_cancel:{third_id}", user_id=999)
    await admin.cb_admin_pre_cancel(cb)
    cb.message.edit_text.assert_awaited()

    cb = make_callback(f"admin_cancel:{third_id}", user_id=999)
    await admin.cb_admin_cancel_booking(cb, bot)
    cb.message.edit_text.assert_awaited()


async def test_admin_services_and_settings_flows(db, monkeypatch):
    import config
    from handlers import admin

    _admin(monkeypatch)
    monkeypatch.setattr(config, "SERVICES", {"TestSvc": 1000})
    monkeypatch.setattr(config, "SERVICE_DURATIONS", {"TestSvc": 30})
    monkeypatch.setattr(config, "WORKING_HOURS", dict(config.WORKING_HOURS))
    monkeypatch.setattr(config, "TIME_SLOTS", list(config.TIME_SLOTS))

    with patch.object(admin.config, "save_config_to_db", AsyncMock()):
        cb = make_callback("admin_services", user_id=999)
        await admin.cb_admin_services(cb)
        cb.message.edit_text.assert_awaited()

        state = make_fsm()
        await admin.cb_admin_add_service(make_callback("admin_add_service", user_id=999), state)
        assert state.set_state.await_count == 1

        msg = make_message("NewSvc, 2000, 60", user_id=999)
        await admin.handle_add_service(msg, state)
        assert config.SERVICES["NewSvc"] == 2000

        cb = make_callback("admin_service_detail:NewSvc", user_id=999)
        await admin.cb_admin_service_detail(cb)
        cb.message.edit_text.assert_awaited()

        state = make_fsm(data={"service_name": "NewSvc"})
        await admin.cb_admin_edit_service(make_callback("admin_edit_service:NewSvc", user_id=999), state)
        await admin.handle_edit_service(make_message("RenamedSvc, 2500, 60", user_id=999), state)
        assert "RenamedSvc" in config.SERVICES

        cb = make_callback("admin_remove_service:RenamedSvc", user_id=999)
        await admin.cb_admin_remove_service(cb)
        cb.message.edit_text.assert_awaited()

        cb = make_callback("admin_confirm_remove_service:RenamedSvc", user_id=999)
        await admin.cb_admin_confirm_remove_service(cb)
        assert "RenamedSvc" not in config.SERVICES

        cb = make_callback("admin_settings", user_id=999)
        await admin.cb_admin_settings(cb)
        cb.message.edit_text.assert_awaited()

        settings_handlers = [
            (admin.cb_admin_change_address, admin.handle_change_address, "Новый адрес"),
            (admin.cb_admin_change_phone, admin.handle_change_phone, "+7 700 000 00 00"),
            (admin.cb_admin_change_hours, admin.handle_change_hours, "Пн-Сб: 09:00-18:00, Вс: 10:00-16:00"),
            (admin.cb_admin_change_salon_name, admin.handle_change_salon_name, "Новая студия"),
            (admin.cb_admin_change_master_name, admin.handle_change_master_name, "Анна"),
            (admin.cb_admin_change_master_desc, admin.handle_change_master_desc, "Описание"),
            (admin.cb_admin_change_master_exp, admin.handle_change_master_exp, "6 лет"),
        ]
        for cb_handler, msg_handler, text in settings_handlers:
            state = make_fsm()
            await cb_handler(make_callback("settings", user_id=999), state)
            msg = make_message(text, user_id=999)
            await msg_handler(msg, state)
            msg.bot.send_message.assert_awaited()


async def test_admin_portfolio_and_social_flows(db, monkeypatch):
    from handlers import admin

    _admin(monkeypatch)
    state = make_fsm()
    cb = make_callback("admin_portfolio", user_id=999)
    await admin.cb_admin_portfolio(cb, state)
    cb.message.edit_text.assert_awaited()

    cb = make_callback("admin_add_portfolio_photo", user_id=999)
    await admin.cb_admin_add_portfolio_photo(cb, state)
    state.set_state.assert_awaited()

    photo = MagicMock(file_id="file-1", file_size=1024)
    msg = make_message("", user_id=999)
    msg.photo = [photo]
    msg.caption = "caption"
    await admin.handle_add_portfolio_photo(msg, state)
    msg.bot.send_message.assert_awaited()

    cb = make_callback("admin_delete_portfolio_photo", user_id=999)
    await admin.cb_admin_delete_portfolio_photo(cb)
    cb.message.edit_text.assert_awaited()

    cb = make_callback("admin_confirm_delete_photo:1", user_id=999)
    await admin.cb_admin_confirm_delete_photo(cb)
    cb.message.edit_text.assert_awaited()

    cb = make_callback("admin_delete_photo_confirm:1", user_id=999)
    await admin.cb_admin_delete_photo_confirm(cb)
    cb.message.edit_text.assert_awaited()

    state = make_fsm()
    cb = make_callback("admin_social_links", user_id=999)
    await admin.cb_admin_social_links(cb, state)
    cb.message.edit_text.assert_awaited()

    cb = make_callback("admin_add_social_link", user_id=999)
    await admin.cb_admin_add_social_link(cb, state)
    state.set_state.assert_awaited()

    msg = make_message("Instagram, https://example.com", user_id=999)
    await admin.handle_add_social_link(msg, state)
    msg.bot.send_message.assert_awaited()

    cb = make_callback("admin_delete_social_link:1", user_id=999)
    await admin.cb_admin_delete_social_link(cb)
    cb.message.edit_text.assert_awaited()
