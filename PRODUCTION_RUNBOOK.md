# Production Runbook

Release: `v1.0.0`

This runbook describes the production deployment and recovery process for Nailshop Bot. Use disposable databases for restore drills. Never run restore drills against the production database.

## 1. Подготовка Сервера

Минимальные требования:

- Linux VPS or managed Docker host.
- Docker Engine installed and running.
- Docker Compose plugin installed.
- Outbound internet access to Telegram API.
- PostgreSQL 16 for production data.
- Optional Redis for production FSM storage, especially for webhook or multi-instance deployments.

Проверка сервера:

```powershell
docker version
docker compose version
```

Подготовка каталога:

```powershell
Set-Location -LiteralPath "C:\path\to\nailshop_deploy"
```

На Linux используйте эквивалент:

```bash
cd /opt/nailshop_deploy
```

## 2. Настройка .env

Создайте `.env` из шаблона:

```powershell
Copy-Item -LiteralPath ".env.example" -Destination ".env"
```

Обязательные production значения:

```env
BOT_TOKEN=replace_with_botfather_token
ADMIN_IDS=123456789
APP_ENV=production
BOT_MODE=polling
DATABASE_URL=postgresql://nailshop:strong_password@db:5432/nailshop
POSTGRES_PASSWORD=strong_password
TIMEZONE=Asia/Almaty
REQUIRE_REDIS_FSM=false
```

Для webhook production дополнительно:

```env
BOT_MODE=webhook
WEBHOOK_URL=https://your-public-domain.example
WEBHOOK_PATH=/webhook
SECRET_TOKEN=replace_with_long_random_secret
REDIS_URL=redis://:password@redis-host:6379/0
REQUIRE_REDIS_FSM=true
```

Правила безопасности:

- Не коммитьте `.env`.
- Не используйте `changeme` в production.
- `POSTGRES_PASSWORD` должен совпадать с паролем в `DATABASE_URL` для bundled compose PostgreSQL.
- `POSTGRES_RESTORE_TEST_DATABASE_URL` должен указывать только на disposable restore database.

## 3. Docker Build

```powershell
docker build --no-cache -t nailshop-bot:v1.0.0 .
docker compose build --no-cache
```

Ожидаемый результат:

- Image builds successfully.
- `postgresql-client` installed in bot image.
- No virtualenv, `.env`, backup, database, cache, or ZIP artifacts are sent into the Docker context.

## 4. Docker Compose Up

```powershell
docker compose up -d
```

Проверка статуса:

```powershell
docker compose ps
```

Ожидаемый результат:

- `db` is healthy.
- `bot` is running.

Если нужно применить schema initialization вручную:

```powershell
docker compose exec bot python migrate_db.py
```

## 5. Проверка Healthcheck

Внутренний healthcheck:

```powershell
docker compose exec bot python healthcheck.py
```

Docker health status:

```powershell
docker inspect --format "{{.State.Health.Status}}" $(docker compose ps -q bot)
```

Webhook mode HTTP checks:

```powershell
Invoke-WebRequest -Uri "https://your-public-domain.example/health"
Invoke-WebRequest -Uri "https://your-public-domain.example/ready"
```

Ожидаемый результат:

- `healthcheck ok`.
- Docker health status is `healthy`.
- Webhook health endpoints return HTTP 200.

## 6. Smoke Test

Telegram checks:

1. Send `/start` to the bot.
2. Open main menu.
3. Open services/prices.
4. Create a test booking for a future slot.
5. Confirm admin receives notification.
6. Cancel the test booking.
7. Open `/admin` from an `ADMIN_IDS` account.
8. Check active bookings and settings.

Do not run smoke tests with a real customer slot unless the owner approves it.

## 7. PostgreSQL Backup

Create backup:

```powershell
docker compose exec bot python backup.py
```

Expected backup path inside container:

```text
/app/backups/nailshop_YYYYMMDD_HHMMSS.sql.gz
```

List backups:

```powershell
docker compose exec bot sh -c "ls -lah /app/backups"
```

If S3 is configured, verify the object exists in:

```text
S3_BUCKET/S3_BACKUP_PREFIX/<filename>
```

## 8. PostgreSQL Restore

### Restore Drill To Disposable DB

Create disposable restore DB in bundled compose PostgreSQL:

```powershell
docker compose exec db psql -U nailshop -d postgres -c "DROP DATABASE IF EXISTS nailshop_restore_test"
docker compose exec db psql -U nailshop -d postgres -c "CREATE DATABASE nailshop_restore_test OWNER nailshop"
```

Run restore check:

```powershell
docker compose exec -e POSTGRES_RESTORE_TEST_DATABASE_URL="postgresql://nailshop:strong_password@db:5432/nailshop_restore_test" bot python restore_check.py /app/backups/<backup-file>.sql.gz --postgres-restore-url "postgresql://nailshop:strong_password@db:5432/nailshop_restore_test"
```

Expected result:

```text
restore-check: ok
```

### Actual Production Restore

Use this only during disaster recovery or planned migration.

1. Stop the bot:

```powershell
docker compose stop bot
```

2. Create a final copy of the current DB if possible.

3. Restore dump into the target production DB:

```powershell
docker compose exec bot sh -c 'gzip -dc /app/backups/<backup-file>.sql.gz | psql "postgresql://nailshop:strong_password@db:5432/nailshop" -v ON_ERROR_STOP=1'
```

4. Start the bot:

```powershell
docker compose up -d bot
docker compose exec bot python healthcheck.py
```

## 9. Проверка Логов

Bot logs:

```powershell
docker compose logs --tail=200 bot
```

Database logs:

```powershell
docker compose logs --tail=200 db
```

Look for:

- `Database initialized`.
- `healthcheck ok`.
- No repeated Telegram conflict errors.
- No PostgreSQL connection errors.
- No backup/restore errors.

## 10. Обновление Версии

Before update:

```powershell
docker compose exec bot python backup.py
docker compose ps
```

Update files:

```powershell
git pull
docker compose build --no-cache
docker compose up -d
docker compose exec bot python healthcheck.py
```

If deploying from ZIP:

1. Save `.env` and backups outside the release folder.
2. Replace application files with the new release ZIP contents.
3. Restore `.env`.
4. Run build/up/healthcheck.

## 11. Rollback

Rollback application only:

```powershell
docker compose stop bot
git checkout <previous-good-tag>
docker compose build --no-cache bot
docker compose up -d bot
docker compose exec bot python healthcheck.py
```

Rollback from ZIP:

1. Stop `bot`.
2. Restore previous release folder.
3. Restore the saved `.env`.
4. Run `docker compose build --no-cache`.
5. Run `docker compose up -d`.

Rollback database:

- Restore the latest known-good backup only if the failed release changed data in a way that must be reverted.
- Always keep the failed-state DB backup before overwriting production.

## 12. Recovery После Сбоя

If bot is down:

```powershell
docker compose ps
docker compose logs --tail=200 bot
docker compose exec bot python healthcheck.py
```

If PostgreSQL is down:

```powershell
docker compose ps db
docker compose logs --tail=200 db
docker compose restart db
```

If database is corrupted or missing:

1. Stop bot.
2. Identify latest valid backup.
3. Restore into PostgreSQL.
4. Run `python healthcheck.py`.
5. Start bot and run Telegram smoke test.

If Telegram polling conflict appears:

- Ensure only one polling instance is running.
- Stop duplicate containers or old deployments.
- For webhook deploy, ensure `BOT_MODE=webhook` and the public URL is correct.

## 13. Что Делать При Ошибках

Common checks:

```powershell
docker compose ps
docker compose logs --tail=200 bot
docker compose logs --tail=200 db
docker compose exec bot python healthcheck.py
```

Typical causes:

| Symptom | Likely Cause | Action |
|---------|--------------|--------|
| `BOT_TOKEN is not set` | Missing `.env` or empty token | Fill `BOT_TOKEN`, restart bot |
| PostgreSQL connection refused | Wrong `DATABASE_URL`, DB not healthy, wrong password | Check `POSTGRES_PASSWORD`, `DATABASE_URL`, `docker compose ps db` |
| `SECRET_TOKEN is required` | Production webhook without secret | Set `SECRET_TOKEN` |
| Restore drill failed | Restore URL points to bad DB or dump invalid | Recreate disposable DB and rerun restore check |
| Telegram conflict | Another polling instance active | Stop duplicate bot instance |
| Bot unhealthy after update | Config, DB, or schema issue | Check logs, run `migrate_db.py`, rollback if needed |

Escalation package for developer/support:

- Current `.env` with secrets redacted.
- `docker compose ps` output.
- Last 200 bot logs.
- Last 200 db logs.
- Exact release version and Git tag.
- Latest backup filename and restore-check result.
