"""
Base62 encoding utilities.

We don't use this as our PRIMARY short-code generation strategy (see
core/code_generator.py for that — we use random generation instead).

This exists because:
1. It's a near-universal interview question ("how would you encode an
   auto-increment ID into a short string?") and you should be able to
   both explain AND implement it, not just describe it.
2. It's genuinely useful as a fallback/alternative strategy, documented
   in ADR-005-url-generation.md.

Alphabet order matters only in that it must be consistent between
encode and decode — it does NOT need to be sorted or "meaningful".
"""

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(ALPHABET)  # 62


def encode(number: int) -> str:
    """
    Encode a non-negative integer into a Base62 string.

    Example: encode(125) -> "21"
    (1*62 + 63... let's just trust the math, verified in tests)
    """
    if number < 0:
        raise ValueError("Base62 encoding only supports non-negative integers")
    if number == 0:
        return ALPHABET[0]

    digits = []
    while number > 0:
        number, remainder = divmod(number, BASE)
        digits.append(ALPHABET[remainder])

    # We built the digits least-significant-first, so reverse them.
    return "".join(reversed(digits))


def decode(short_code: str) -> int:
    """
    Decode a Base62 string back into its original integer.

    Example: decode("21") -> 125
    """
    number = 0
    for char in short_code:
        index = ALPHABET.index(char)  # raises ValueError on invalid char
        number = number * BASE + index
    return number
