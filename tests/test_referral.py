
"""Tests for new referral storage functions:
  - storage.ensure_user_ref_code()
  - storage.get_referral_count()
  - storage.add_referral()  (existing, tested in context)
  - storage.add_bonus()  (used by referral flow)
"""
import pytest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


class TestEnsureUserRefCode:

    async def test_creates_ref_code_for_new_user(self, db):
        """User with no loyalty record gets a ref_code created on demand."""
        import storage
        await storage.save_user(201, username="u1", first_name="Alice")
        ref_code = await storage.ensure_user_ref_code(201, "Alice")
        assert ref_code is not None
        assert len(ref_code) == 8
        assert ref_code.isalnum()

    async def test_creates_ref_code_for_user_without_loyalty(self, db):
        """User with loyalty row but no ref_code gets one assigned."""
        import storage
        await storage.save_user(202, username="u2", first_name="Bob")
        import db as _db
        async with _db.acquire() as conn:
            from tz_utils import get_now
            import config
            now = get_now(config.TIMEZONE).isoformat()
            await conn.execute(
                "INSERT OR IGNORE INTO loyalty (telegram_id, name, visits, bonuses, updated_at) "
                "VALUES (?, ?, 0, 0, ?)",
                202, "Bob", now,
            )
            await conn.commit()
        ref_code = await storage.ensure_user_ref_code(202, "Bob")
        assert ref_code is not None
        assert len(ref_code) == 8

    async def test_returns_existing_ref_code_unchanged(self, db):
        """Calling ensure twice returns the same code - idempotent."""
        import storage
        await storage.save_user(203, username="u3", first_name="Carol")
        code1 = await storage.ensure_user_ref_code(203, "Carol")
        code2 = await storage.ensure_user_ref_code(203, "Carol")
        assert code1 == code2

    async def test_ref_code_unique_across_users(self, db):
        """Different users get different ref_codes."""
        import storage
        for uid in range(210, 220):
            await storage.save_user(uid, username=f"u{uid}", first_name=f"User{uid}")
        codes = [await storage.ensure_user_ref_code(uid, f"User{uid}") for uid in range(210, 220)]
        assert len(set(codes)) == len(codes), "Duplicate ref_codes detected"

    async def test_ref_code_stored_in_loyalty(self, db):
        """After ensure_user_ref_code, loyalty row must have the same ref_code."""
        import storage
        await storage.save_user(204, username="u4", first_name="Dave")
        ref_code = await storage.ensure_user_ref_code(204, "Dave")
        loyalty = await storage.get_loyalty(204)
        assert loyalty is not None
        assert loyalty["ref_code"] == ref_code

    async def test_get_user_by_ref_code_after_ensure(self, db):
        """ensure_user_ref_code + get_user_by_ref_code roundtrip."""
        import storage
        await storage.save_user(205, username="u5", first_name="Eve")
        ref_code = await storage.ensure_user_ref_code(205, "Eve")
        found = await storage.get_user_by_ref_code(ref_code)
        assert found is not None
        assert found["telegram_id"] == 205

    async def test_returns_string(self, db):
        """Return type must always be str, never None."""
        import storage
        await storage.save_user(206, username="u6", first_name="Frank")
        result = await storage.ensure_user_ref_code(206, "Frank")
        assert isinstance(result, str)
        assert result


class TestGetReferralCount:

    async def test_zero_for_new_user(self, db):
        import storage
        await storage.save_user(301, username="ref1", first_name="R1")
        assert await storage.get_referral_count(301) == 0

    async def test_counts_single_referral(self, db):
        import storage
        await storage.save_user(302, username="referrer", first_name="Referrer")
        await storage.save_user(303, username="referee", first_name="Referee")
        await storage.ensure_user_ref_code(302, "Referrer")
        success = await storage.add_referral(302, 303)
        assert success is True
        assert await storage.get_referral_count(302) == 1

    async def test_counts_multiple_referrals(self, db):
        import storage
        await storage.save_user(310, username="r0", first_name="R0")
        await storage.ensure_user_ref_code(310, "R0")
        for uid in range(311, 316):
            await storage.save_user(uid, username=f"ref{uid}", first_name=f"Ref{uid}")
            await storage.add_referral(310, uid)
        assert await storage.get_referral_count(310) == 5

    async def test_referee_count_is_zero(self, db):
        import storage
        await storage.save_user(320, username="rA", first_name="A")
        await storage.save_user(321, username="rB", first_name="B")
        await storage.ensure_user_ref_code(320, "A")
        await storage.add_referral(320, 321)
        assert await storage.get_referral_count(321) == 0

    async def test_duplicate_referral_not_counted_twice(self, db):
        import storage
        await storage.save_user(330, username="rX", first_name="X")
        await storage.save_user(331, username="rY", first_name="Y")
        await storage.ensure_user_ref_code(330, "X")
        await storage.add_referral(330, 331)
        dup = await storage.add_referral(330, 331)
        assert dup is False
        assert await storage.get_referral_count(330) == 1

    async def test_self_referral_rejected(self, db):
        import storage
        await storage.save_user(340, username="rZ", first_name="Z")
        await storage.ensure_user_ref_code(340, "Z")
        result = await storage.add_referral(340, 340)
        assert result is False
        assert await storage.get_referral_count(340) == 0


class TestReferralBonusFlow:

    async def test_bonus_added_to_referrer(self, db):
        import storage, config
        await storage.save_user(401, username="r1", first_name="R1")
        await storage.ensure_user_ref_code(401, "R1")
        await storage.add_bonus(401, config.REFERRAL_BONUS)
        loyalty = await storage.get_loyalty(401)
        assert loyalty["bonuses"] >= config.REFERRAL_BONUS

    async def test_add_bonus_without_loyalty_returns_false(self, db):
        import storage
        result = await storage.add_bonus(999999, 100)
        assert result is False

    async def test_ref_code_lookup_missing_returns_none(self, db):
        import storage
        result = await storage.get_user_by_ref_code("00000000")
        assert result is None
