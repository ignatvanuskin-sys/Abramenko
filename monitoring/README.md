# Production monitoring

The bot exposes `/health`, `/ready`, and `/metrics` from the configured HTTP port (`WEBHOOK_PORT`, or `PORT` when supplied). The endpoints are available in both polling and webhook mode.

## Prometheus and Grafana

For a local production-like stack, create `.env` from `.env.example`, set `METRICS_TOKEN` only if the Prometheus server will send a bearer token, and set a strong `GRAFANA_ADMIN_PASSWORD`. Start the observability profile with:

```bash
docker compose --profile monitoring up -d --build
```

Prometheus is available on port `9090`, Grafana on port `3000`, and the dashboard `nail-tg production` is provisioned automatically. The default scrape file is `monitoring/prometheus.yml`. If `METRICS_TOKEN` is set, mount the token as `/etc/prometheus/secrets/metrics_token` and uncomment the authorization block in that file.

For a remote deployment, expose only Prometheus/Grafana through the platform's private network or an authenticated reverse proxy. Do not publish the metrics token, Sentry DSN, or Grafana password in Git.

## Sentry

Set `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, and `SENTRY_RELEASE` in the production secret store. The bot initializes Sentry before creating the Telegram client and captures unhandled update exceptions and fatal runtime exceptions. `send_default_pii` is disabled; request headers, cookies, query strings, Telegram identifiers, phone numbers, usernames, tokens, passwords, and DSNs are filtered before sending.

A conservative default trace sample rate of `0.05` is used. Set `SENTRY_TRACES_SAMPLE_RATE=0` when traces are not needed, and increase it only after checking project quotas. Use a release value tied to the deployed Git commit, for example `nail-tg@cc2903a`.

## Recommended alerts

Configure alerts in Prometheus/Grafana for `bot_errors_total` increasing over five minutes, `bot_reminders_failed` or `bot_broadcast_failed` increasing, `bot_uptime_seconds` resetting unexpectedly, and the `/health` endpoint returning HTTP 503. The persistent counters survive process restarts; rate-based alerts should use `increase(metric[5m])`.
