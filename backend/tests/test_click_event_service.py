import json

from app.services.click_event_service import ClickEventService


class RecordingPublisher:
    def __init__(self):
        self.published: list[bytes] = []

    def publish(self, body: bytes) -> None:
        self.published.append(body)


class AlwaysRaisingPublisher:
    def publish(self, body: bytes) -> None:
        raise RuntimeError("simulated broker failure")


def test_record_click_publishes_exactly_one_message():
    publisher = RecordingPublisher()
    service = ClickEventService(publisher)

    service.record_click(url_id=42, user_agent_string="some-ua", referrer="https://google.com")

    assert len(publisher.published) == 1


def test_published_message_contains_correct_fields():
    publisher = RecordingPublisher()
    service = ClickEventService(publisher)

    service.record_click(url_id=42, user_agent_string="some-ua", referrer="https://google.com")

    payload = json.loads(publisher.published[0])
    assert payload["url_id"] == 42
    assert payload["user_agent"] == "some-ua"
    assert payload["referrer"] == "https://google.com"
    assert "event_id" in payload  # needed for Phase 7 idempotent consumption
    assert "occurred_at" in payload


def test_published_user_agent_is_raw_not_pre_parsed():
    """Confirms the Phase 6 design decision: the producer does NOT
    parse the User-Agent (device/browser/os) -- that work moved to
    core/user_agent_parser.py, used by the Phase 7 worker instead. The
    published message should carry the raw header string only."""
    publisher = RecordingPublisher()
    service = ClickEventService(publisher)

    raw_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
    service.record_click(url_id=1, user_agent_string=raw_ua, referrer=None)

    payload = json.loads(publisher.published[0])
    assert payload["user_agent"] == raw_ua
    assert "device_type" not in payload
    assert "browser" not in payload
    assert "os" not in payload


def test_each_call_gets_a_unique_event_id():
    publisher = RecordingPublisher()
    service = ClickEventService(publisher)

    service.record_click(url_id=1, user_agent_string=None, referrer=None)
    service.record_click(url_id=1, user_agent_string=None, referrer=None)

    event_ids = [json.loads(m)["event_id"] for m in publisher.published]
    assert event_ids[0] != event_ids[1]


def test_publish_failure_does_not_raise():
    """
    THE fail-open test for the producer side, same philosophy as
    RedisCache (Phase 4): a broker outage at publish time must not
    propagate into the caller -- the redirect must still succeed. See
    the Phase 5/6 checkpoint discussion for what this does and doesn't
    cover now that we've moved past Phase 5's direct-DB-write design.
    """
    publisher = AlwaysRaisingPublisher()
    service = ClickEventService(publisher)

    # Must NOT raise.
    service.record_click(url_id=1, user_agent_string="some-ua", referrer=None)
