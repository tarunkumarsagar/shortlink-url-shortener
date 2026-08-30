import json

import pytest

from app.workers.event_processor import (
    ClickEventProcessor,
    MalformedMessageError,
    ProcessingError,
)

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


class RecordingRepository:
    def __init__(self, insert_result: bool = True):
        self.calls = []
        self._insert_result = insert_result

    def insert_idempotent(self, event_id, url_id, referrer, user_agent, device_type, browser, os):
        self.calls.append(
            dict(event_id=event_id, url_id=url_id, referrer=referrer, user_agent=user_agent,
                 device_type=device_type, browser=browser, os=os)
        )
        return self._insert_result


class AlwaysRaisingRepository:
    def insert_idempotent(self, **kwargs):
        raise RuntimeError("simulated database failure")


def make_message(**overrides) -> bytes:
    payload = {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "url_id": 42,
        "user_agent": IPHONE_UA,
        "referrer": "https://google.com",
        "occurred_at": "2026-01-01T00:00:00+00:00",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def test_processes_a_valid_message_and_writes_parsed_fields():
    repo = RecordingRepository()
    processor = ClickEventProcessor(repo)

    processor.process(make_message())

    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["event_id"] == "11111111-1111-1111-1111-111111111111"
    assert call["url_id"] == 42
    assert call["referrer"] == "https://google.com"
    assert call["device_type"] == "mobile"  # PARSING now happens here, in the worker
    assert call["os"] == "iOS"


def test_invalid_json_raises_malformed_message_error():
    repo = RecordingRepository()
    processor = ClickEventProcessor(repo)

    with pytest.raises(MalformedMessageError):
        processor.process(b"this is not json {{{")

    assert repo.calls == []  # never even attempted the DB write


def test_missing_required_field_raises_malformed_message_error():
    repo = RecordingRepository()
    processor = ClickEventProcessor(repo)

    body = json.dumps({"user_agent": IPHONE_UA}).encode("utf-8")  # no event_id, no url_id

    with pytest.raises(MalformedMessageError):
        processor.process(body)

    assert repo.calls == []


def test_missing_user_agent_and_referrer_are_tolerated():
    """Only event_id and url_id are required -- user_agent/referrer are
    genuinely optional fields, same as in the producer."""
    repo = RecordingRepository()
    processor = ClickEventProcessor(repo)

    body = json.dumps({"event_id": "abc", "url_id": 1}).encode("utf-8")
    processor.process(body)  # must not raise

    assert repo.calls[0]["user_agent"] is None
    assert repo.calls[0]["referrer"] is None
    assert repo.calls[0]["device_type"] is None


def test_repository_failure_raises_processing_error_not_malformed():
    """
    Distinguishes the two failure classes: a database problem is
    TRANSIENT (ProcessingError, worth retrying) -- it is NOT the same
    as a MalformedMessageError (permanently broken, never worth
    retrying). The worker's retry/DLQ policy depends on telling these
    apart correctly.
    """
    repo = AlwaysRaisingRepository()
    processor = ClickEventProcessor(repo)

    with pytest.raises(ProcessingError):
        processor.process(make_message())


def test_duplicate_event_id_does_not_raise():
    """
    THE idempotency test directly answering the Phase 6/7 checkpoint:
    when the repository reports the event_id already existed
    (insert_idempotent returns False, simulating RabbitMQ redelivery
    of an already-processed message), processing must complete
    successfully -- NOT raise -- so the worker acks it and moves on,
    rather than endlessly retrying a message that was already handled.
    """
    repo = RecordingRepository(insert_result=False)  # simulates ON CONFLICT DO NOTHING
    processor = ClickEventProcessor(repo)

    processor.process(make_message())  # must not raise

    assert len(repo.calls) == 1  # the attempt was still made, correctly


def test_processing_the_same_message_twice_is_safe():
    """
    Simulates an actual redelivery at the processor level: process()
    is called twice with the IDENTICAL raw message bytes (as would
    happen if RabbitMQ redelivers). Both calls must succeed without
    raising -- the real deduplication guarantee comes from the
    database's UNIQUE constraint (tested for real in
    test_click_event_repository_idempotency.py), but this confirms the
    processor itself doesn't break on a repeated identical call.
    """
    repo = RecordingRepository(insert_result=True)  # first call: real insert
    processor = ClickEventProcessor(repo)
    message = make_message()

    processor.process(message)  # first delivery
    repo._insert_result = False  # second delivery: DB reports conflict
    processor.process(message)  # redelivery -- must also not raise

    assert len(repo.calls) == 2
    assert repo.calls[0]["event_id"] == repo.calls[1]["event_id"]
