# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import pytest


def test_production_webhook_requires_secret(monkeypatch):
    import config

    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "BOT_MODE", "webhook")
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://bot.example.com")
    monkeypatch.setattr(config, "WEBHOOK_PATH", "/webhook")
    monkeypatch.setattr(config, "WEBHOOK_SECRET_TOKEN", "")

    with pytest.raises(config.ConfigError, match="SECRET_TOKEN"):
        config.validate_runtime_config()


def test_development_webhook_allows_missing_secret(monkeypatch):
    import config

    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "BOT_MODE", "webhook")
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://bot.example.com")
    monkeypatch.setattr(config, "WEBHOOK_PATH", "/webhook")
    monkeypatch.setattr(config, "WEBHOOK_SECRET_TOKEN", "")

    assert config.validate_runtime_config() is True


def test_webhook_url_must_be_https(monkeypatch):
    import config

    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "BOT_MODE", "webhook")
    monkeypatch.setattr(config, "WEBHOOK_URL", "http://bot.example.com")
    monkeypatch.setattr(config, "WEBHOOK_PATH", "/webhook")

    with pytest.raises(config.ConfigError, match="https"):
        config.validate_runtime_config()


def test_webhook_url_requires_public_hostname(monkeypatch):
    import config

    monkeypatch.setattr(config, "WEBHOOK_URL", "https://localhost")

    with pytest.raises(config.ConfigError, match="public hostname"):
        config.validate_webhook_url()


def test_polling_does_not_require_webhook_settings(monkeypatch):
    import config

    monkeypatch.setattr(config, "BOT_MODE", "polling")
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "WEBHOOK_URL", "")
    monkeypatch.setattr(config, "WEBHOOK_SECRET_TOKEN", "")

    assert config.validate_runtime_config() is True


def test_malformed_admin_id_has_clear_startup_error(monkeypatch):
    import config

    monkeypatch.setattr(config, "BOT_MODE", "polling")
    monkeypatch.setattr(config, "INVALID_ADMIN_IDS", ["not-a-chat"])

    with pytest.raises(config.ConfigError, match="ADMIN_CHAT_ID must be an integer"):
        config.validate_runtime_config()


def test_invalid_bot_mode_has_clear_error(monkeypatch):
    import config

    monkeypatch.setattr(config, "BOT_MODE", "worker")

    with pytest.raises(config.ConfigError, match="BOT_MODE"):
        config.validate_runtime_config()
