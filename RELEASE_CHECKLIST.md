# Release Checklist

Release: `v1.0.0`

Use this checklist immediately before creating the Git tag and delivering the release ZIP.

☐ Docker build

☐ Docker compose

☐ Healthcheck

☐ PostgreSQL

☐ Backup

☐ Restore

☐ Tests

☐ Coverage

☐ Import

☐ Packaging

☐ Documentation

☐ Version

☐ Git Tag

☐ Release ZIP

## Expected Evidence For v1.0.0

☐ `docker build --no-cache -t nailshop-bot:v1.0.0 .` passes

☐ `docker compose build --no-cache` passes

☐ `docker compose up -d` starts required services

☐ `docker compose exec bot python healthcheck.py` returns success

☐ PostgreSQL integration tests pass against disposable DB

☐ PostgreSQL `.sql.gz` restore drill passes against disposable DB

☐ `py -m pytest --cov -v` passes

☐ Coverage result recorded

☐ `py -m compileall bot.py config.py storage.py handlers scheduler.py backup.py restore_check.py healthcheck.py` passes

☐ `python -c "import bot; import restore_check; import healthcheck"` passes

☐ Release ZIP does not contain `.env`, `.git`, virtualenvs, runtime DBs, backups, caches, or nested ZIPs

☐ README, CHANGELOG, PRODUCTION_RUNBOOK, RELEASE_CHECKLIST, RELEASE_NOTES, and LICENSE are present

☐ Version `1.0.0` is documented in README, CHANGELOG, and release notes

☐ Git tag `v1.0.0` is created after final review

## Manual Owner Checks

☐ Real `BOT_TOKEN` is set

☐ Real `ADMIN_IDS` are set

☐ Production `DATABASE_URL` is set

☐ `POSTGRES_PASSWORD` is not `changeme`

☐ Webhook settings are set if `BOT_MODE=webhook`

☐ Redis is configured if required for production mode

☐ First Telegram smoke test is completed by owner/admin
