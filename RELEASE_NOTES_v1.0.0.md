# Release Notes v1.0.0

Nailshop Bot `v1.0.0` is the first production-ready commercial release.

## Included

- Telegram client booking flow for a single nail master.
- Admin panel for bookings, services, settings, portfolio, social links, unavailable periods, privacy export/delete, and CSV export.
- Duration-aware booking availability and overlap protection.
- PostgreSQL production storage with SQLite local/dev fallback.
- Dockerfile and Docker Compose deployment path.
- Runtime healthcheck for container and database readiness.
- PostgreSQL backup via `pg_dump` and real restore drill via `psql`.
- Local backup support and optional S3-compatible offsite backup upload.
- Scheduler reminders, review requests, auto-completion, cleanup, and DB-backed scheduler locks.
- Privacy retention and anonymization controls.
- Production runbook, release checklist, changelog, and proprietary license notice.

## Verified Before Release

- Static audit completed.
- Production audit completed.
- Docker build passed.
- Docker Compose validation passed.
- Runtime healthcheck passed.
- PostgreSQL integration tests passed.
- PostgreSQL backup and restore drill passed.
- Compileall passed.
- Import checks passed.
- Full test suite: `342 passed, 3 skipped`.
- Coverage: `65%`.
- Release ZIP validation: no secrets, virtualenvs, runtime data, backups, databases, caches, or nested ZIPs.

## Runtime Requirements

- Python runtime in Docker: `python:3.10-slim`.
- Tested locally with Python 3.11.
- PostgreSQL 16 recommended for production.
- Docker and Docker Compose required for the standard deployment path.
- Redis recommended for webhook or multi-instance production deployments.

## Manual Steps Before First Production Deploy

- Fill real `.env` values.
- Replace `POSTGRES_PASSWORD=changeme`.
- Set `BOT_TOKEN` from @BotFather.
- Set correct `ADMIN_IDS`.
- Configure `DATABASE_URL` for production PostgreSQL.
- Configure webhook and `SECRET_TOKEN` if using webhook mode.
- Run `docker compose exec bot python healthcheck.py`.
- Run a Telegram smoke test from an admin account.

## Git Release Commands

Prepare and run manually after final owner approval:

```powershell
git add .
git commit -m "Release v1.0.0"
git tag v1.0.0
git push
git push origin v1.0.0
```
