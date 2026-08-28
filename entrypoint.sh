#!/bin/sh
set -e

# ─────────────────────────────────────────────────────────────────────────────
# Railway Fix: volume монтируется ПОСЛЕ Docker build-слоёв, поэтому
# permissions директории /app/data сбрасываются до root:root 755.
# Этот скрипт запускается как root и явно выставляет нужные права ДО старта бота.
# ─────────────────────────────────────────────────────────────────────────────

mkdir -p /app/data /app/backups /app/logs
# Mounted volumes may be recreated as root:root by the platform. Fix ownership
# while still privileged, then run the application as the unprivileged bot user.
chown -R bot:bot /app/data /app/backups /app/logs
chmod 750 /app/data /app/backups /app/logs

echo "[entrypoint] Runtime directories prepared"
exec gosu bot "$@"
