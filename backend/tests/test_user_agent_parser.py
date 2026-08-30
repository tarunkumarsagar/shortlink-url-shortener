from app.core.user_agent_parser import parse_user_agent

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DESKTOP_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def test_parses_mobile_device_type_from_iphone_user_agent():
    result = parse_user_agent(IPHONE_UA)
    assert result.device_type == "mobile"
    assert result.os == "iOS"
    assert "Safari" in result.browser


def test_parses_desktop_device_type_from_chrome_user_agent():
    result = parse_user_agent(DESKTOP_CHROME_UA)
    assert result.device_type == "desktop"
    assert result.browser == "Chrome"
    assert result.os == "Windows"


def test_none_user_agent_returns_all_none_fields():
    result = parse_user_agent(None)
    assert result.device_type is None
    assert result.browser is None
    assert result.os is None


def test_empty_string_user_agent_returns_all_none_fields():
    result = parse_user_agent("")
    assert result.device_type is None
    assert result.browser is None
    assert result.os is None


def test_malformed_user_agent_does_not_raise():
    """Garbage input must degrade to unparsed fields, never crash the
    caller (currently: tests; from Phase 7 onward: the analytics
    worker processing a queue of real, messy, client-supplied
    strings)."""
    result = parse_user_agent("not a real user agent %%% \x00\x01")
    # We don't assert exact values here (the underlying library's
    # fallback behavior for garbage input isn't a contract we own) --
    # only that calling this never raises, which the test reaching
    # this line already proves.
    assert result is not None
