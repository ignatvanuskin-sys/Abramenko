"""Production observability adapters.

Integrations are optional and fail closed: the bot remains usable when no Sentry
DSN or metrics token is configured, while configuration errors are logged.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_SENSITIVE_KEY = re.compile(r"(token|password|secret|dsn|phone|telegram_id|username|authorization)", re.I)


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[Filtered]" if _SENSITIVE_KEY.search(str(key)) else _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def init_sentry() -> bool:
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("Sentry disabled: SENTRY_DSN is not configured")
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("SENTRY_ENVIRONMENT", os.getenv("APP_ENV", "development")),
            release=os.getenv("SENTRY_RELEASE", "nail-tg@local"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
            profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0")),
            send_default_pii=False,
            before_send=_before_send,
        )
        logger.info("Sentry initialized")
        return True
    except ImportError:
        logger.error("SENTRY_DSN is configured but sentry-sdk is not installed")
    except Exception:
        logger.exception("Sentry initialization failed")
    return False


def _before_send(event: dict, hint: dict) -> dict:
    event = _scrub(event)
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        request.pop("headers", None)
        request.pop("query_string", None)
    return event


def capture_exception(error: BaseException, **context: Any) -> None:
    """Send an exception to Sentry when enabled, without exposing sensitive context."""
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            for key, value in _scrub(context).items():
                scope.set_extra(key, value)
            sentry_sdk.capture_exception(error)
    except ImportError:
        return
    except Exception:
        logger.debug("Unable to report exception to Sentry", exc_info=True)


def prometheus_text(metrics: dict[str, Any]) -> str:
    """Render scalar metrics using Prometheus text exposition format 0.0.4."""
    lines: list[str] = []
    for key, value in metrics.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        name = re.sub(r"[^a-zA-Z0-9_]", "_", f"bot_{key}").lower()
        metric_type = "counter" if key.endswith(("_total", "_created", "_cancelled", "_completed", "_sent", "_failed")) else "gauge"
        lines.extend((f"# HELP {name} nail-tg metric {key}", f"# TYPE {name} {metric_type}", f"{name} {value}"))
    return "\n".join(lines) + "\n"


def metrics_token_valid(headers: dict[str, str]) -> bool:
    expected = os.getenv("METRICS_TOKEN", "").strip()
    if not expected:
        return True
    return headers.get("Authorization", "") == f"Bearer {expected}"
