from app.core.base62 import ALPHABET
from app.core.code_generator import generate_short_code


def test_generated_code_has_default_length():
    assert len(generate_short_code()) == 7


def test_generated_code_respects_custom_length():
    assert len(generate_short_code(length=10)) == 10


def test_generated_code_only_uses_base62_alphabet():
    code = generate_short_code(length=50)  # long code to exercise more of the alphabet
    assert all(char in ALPHABET for char in code)


def test_generated_codes_are_not_all_identical():
    """Not a rigorous randomness test (that's a job for statistical
    test suites, out of scope here) -- just a smoke test that we're
    not accidentally returning a constant string."""
    codes = {generate_short_code() for _ in range(100)}
    assert len(codes) > 90  # allow for astronomically unlikely collisions
