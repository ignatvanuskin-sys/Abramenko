# Changelog

## [1.0.0] - 2026-07-09

### Release Notes

- First production-ready commercial release of Nailshop Bot.
- Release scope: single-master Telegram booking bot with admin panel, services, portfolio, social links, booking lifecycle, reminders, privacy controls, backup/restore, Docker deployment, and PostgreSQL production support.
- Verified release gates: static audit, production audit, Docker build, Docker Compose build/runtime healthcheck, PostgreSQL integration tests, PostgreSQL restore drill, backup/restore, compileall, import checks, and full test suite.
- Final verification state before packaging: `342 passed, 3 skipped`, coverage `65%`.

### Production Readiness

- Booking integrity is protected by `booking_slots` unique constraints, transactional save flow, slot locks, active booking limits, and storage-level validation.
- PostgreSQL backup uses `pg_dump`; restore drills use real `psql` against a disposable PostgreSQL database when configured.
- Docker healthcheck validates configuration, database connectivity, and initialized schema.
- Runtime docs, production runbook, release checklist, and proprietary license notice are included for client handoff.

### Deployment Notes

- Recommended production database: PostgreSQL 16.
- Docker runtime image: `python:3.10-slim` with `postgresql-client` installed.
- CI/local release verification used Python 3.11 for tests.
- Before first production deploy, the owner must provide real `.env` values: `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, PostgreSQL password, and webhook/Redis settings if used.

## Duration-aware booking and unavailable periods

- Added service durations while keeping `SERVICES` as the legacy price map for compatibility.
- Added duration-aware booking checks: a service reserves consecutive 30-minute slices, not just the start slot.
- Added `duration_minutes` to bookings, services, waitlist entries, CSV export, reminders, and user/admin booking details.
- Added unavailable periods for full-day, single-slot, and time-range blocks.
- Added admin UI to list, add, and delete time blocks without cancelling existing bookings.
- Updated waitlist handling so notifications respect the requested duration and newly available range.
- Added idempotent schema updates for duration columns and `unavailable_periods`.
- Added regression tests for overlapping ranges, cancellation, unavailable periods, availability, and waitlist duration.
- Full run after this pass: 294 passed, 0 failed, 50% coverage.

## Test and production readiness pass

- Environment fixed by using Python 3.11 venv instead of unsupported Python 3.14.
- First full run collected 279 tests: 279 passed, 0 failed, 0 collection errors.
- Removed remaining legacy backup artifacts: dump header, backup filenames, Docker healthcheck default DB path.
- Renamed runtime contact config from legacy public brand env names to `SALON_*` while keeping existing DB setting keys.
- Added regression coverage for exclusive slot locks, duplicate booking rejection, user blacklist, admin audit log, admin block/unblock keyboard, and E.164 phone normalization.
- Cleaned backup tests to avoid unawaited coroutine warnings.
- Final full run: 287 passed, 0 failed.
- Final coverage: 48% total.
- Bot import, py_compile, and mocked startup-to-polling check passed.
