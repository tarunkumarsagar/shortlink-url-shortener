import pytest

from app.core.base62 import decode, encode


@pytest.mark.parametrize(
    "number",
    [0, 1, 61, 62, 63, 125, 3843, 99999, 12345678, 999999999999],
)
def test_encode_decode_round_trip(number):
    """The core correctness property: decode(encode(n)) == n for any
    non-negative integer, across small, boundary, and large values."""
    assert decode(encode(number)) == number


def test_encode_zero_is_single_character():
    assert encode(0) == "0"


def test_encode_is_deterministic():
    """Same input must always produce the same output -- Base62 encoding
    is a pure function, unlike our random code generator."""
    assert encode(123456) == encode(123456)


def test_encode_rejects_negative_numbers():
    with pytest.raises(ValueError):
        encode(-1)


def test_encode_output_grows_with_input_size():
    """Sanity check on the log-base-62 growth property we claimed in
    the design doc."""
    assert len(encode(61)) == 1
    assert len(encode(62)) == 2  # crosses the first base-62 boundary
