FROM python:3.10-slim
# UTF-8 - production Dockerfile

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client gosu \
    && rm -rf /var/lib/apt/lists/*

# Railway: create directories at build time.
# entrypoint.sh initializes DB path and volume at runtime.
RUN mkdir -p /app/data /app/backups /app/logs \
    && useradd --system --create-home --home-dir /home/bot --shell /usr/sbin/nologin bot \
    && chown -R bot:bot /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD python healthcheck.py

# Entrypoint starts as root, fixes mounted-volume ownership, then drops privileges.
USER root
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "bot.py"]
