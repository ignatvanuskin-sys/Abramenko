import os

import observability


def test_prometheus_text_uses_valid_names_and_types():
    text = observability.prometheus_text({"errors_total": 2, "uptime_seconds": 4.5, "ignored": "x"})
    assert "# TYPE bot_errors_total counter" in text
    assert "bot_errors_total 2" in text
    assert "# TYPE bot_uptime_seconds gauge" in text
    assert "ignored" not in text


def test_metrics_token_validation(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "secret")
    assert observability.metrics_token_valid({"Authorization": "Bearer secret"})
    assert not observability.metrics_token_valid({"Authorization": "Bearer wrong"})


def test_sentry_event_scrubs_personal_and_request_data():
    event = {
        "user": {"telegram_id": 123, "username": "name"},
        "request": {"headers": {"Authorization": "secret"}, "cookies": {"sid": "x"}},
        "extra": {"phone": "+70000000000"},
    }
    scrubbed = observability._before_send(event, {})
    assert scrubbed["user"]["telegram_id"] == "[Filtered]"
    assert scrubbed["extra"]["phone"] == "[Filtered]"
    assert "headers" not in scrubbed["request"]
    assert "cookies" not in scrubbed["request"]
