#!/bin/sh
set -e

# ─────────────────────────────────────────────────────────────────────────────
# Railway Fix: volume монтируется ПОСЛЕ Docker build-слоёв, поэтому
# permissions директории /app/data сбрасываются до root:root 755.
# Этот скрипт запускается как root и явно выставляет нужные права ДО старта бота.
# ─────────────────────────────────────────────────────────────────────────────

mkdir -p /app/data /app/backups /app/logs
# H-1 FIX: 755 is sufficient — owner (root) can write, others can only read/exec
chmod 755 /app/data /app/backups /app/logs

echo "[entrypoint] Permissions fixed:"
ls -la /app/ | grep -E "data|backups|logs"

exec "$@"
