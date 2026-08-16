from dosadash_bot.config import Settings
from dosadash_bot.render import echo_text, welcome_text


def make_settings(**overrides) -> Settings:
    return Settings(telegram_bot_token="123456:TEST-TOKEN", _env_file=None, **overrides)


def test_welcome_text_personalized():
    assert "Vanakkam Priya" in welcome_text("Priya")
    assert "Vanakkam there" in welcome_text(None)


def test_echo_text():
    assert echo_text("2 masala dosa") == "🥞 Echo: 2 masala dosa"
    assert "only handle text" in echo_text(None)


def test_webhook_url_composed():
    s = make_settings(public_base_url="https://dosadash.venkateshs.dev/")
    assert s.webhook_url == "https://dosadash.venkateshs.dev/tg/webhook"


def test_webhook_secret_derived_and_stable():
    a, b = make_settings(), make_settings()
    assert a.webhook_secret == b.webhook_secret
    assert len(a.webhook_secret) == 32
    assert a.telegram_bot_token not in a.webhook_secret


def test_webhook_secret_explicit_override():
    s = make_settings(telegram_webhook_secret="explicit-secret")
    assert s.webhook_secret == "explicit-secret"
