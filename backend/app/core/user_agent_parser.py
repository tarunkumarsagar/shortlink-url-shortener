"""
User-agent parsing.

PHASE 6 DESIGN NOTE: this logic used to run inline on the redirect
path (Phase 5). It's been extracted here and moved OFF the redirect
path entirely -- the redirect now only builds and publishes a small
JSON message (see services/click_event_service.py); parsing happens
in the Phase 7 analytics worker instead.

WHY move it, not just the DB write: user-agent parsing (via the
user-agents library, itself built on regex-heavy ua-parser rules) is
real CPU work, not I/O wait. Moving only the database write off the
hot path while leaving CPU-bound parsing on it would have been a
half-measure -- the redirect should do the absolute minimum work
needed to respond, and "classify this string with a regex library" is
squarely analytics work, not redirect work.
"""

from dataclasses import dataclass
from typing import Optional

from user_agents import parse as _parse_user_agent


@dataclass
class ParsedUserAgent:
    device_type: Optional[str]
    browser: Optional[str]
    os: Optional[str]


def _classify_device(ua) -> str:
    if ua.is_bot:
        return "bot"
    if ua.is_mobile:
        return "mobile"
    if ua.is_tablet:
        return "tablet"
    if ua.is_pc:
        return "desktop"
    return "unknown"


def parse_user_agent(user_agent_string: Optional[str]) -> ParsedUserAgent:
    """
    Never raises -- a malformed or unrecognized User-Agent string
    degrades to a ParsedUserAgent with all fields None, rather than
    propagating an exception into whatever's calling this (currently:
    tests directly; from Phase 7 onward: the analytics worker, which
    should not crash-and-retry-forever on one bad message because of
    a parsing library edge case).
    """
    if not user_agent_string:
        return ParsedUserAgent(device_type=None, browser=None, os=None)

    try:
        ua = _parse_user_agent(user_agent_string)
        return ParsedUserAgent(
            device_type=_classify_device(ua),
            browser=ua.browser.family,
            os=ua.os.family,
        )
    except Exception:
        return ParsedUserAgent(device_type=None, browser=None, os=None)
