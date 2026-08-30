"""
Short-code generation — our PRIMARY strategy.

DESIGN DECISION (see docs/decisions/ADR-005-url-generation.md):
    We generate a random Base62 string rather than encoding a sequential
    database ID. This is deliberate:

    - Non-enumerable: nobody can guess "the next" short code, unlike a
      sequential ID, which prevents scraping every URL in the system.
    - No shared counter: multiple backend instances can generate codes
      independently with no write contention on a single sequence.
    - Collision handling becomes an explicit, visible piece of the
      design (see repositories/url_repository.py) rather than being
      hidden by DB auto-increment magic — which is exactly the kind of
      thing an interviewer will ask you to reason about.

    Trade-off accepted: a (very small, but non-zero) chance of
    collision on generation, which we must detect and retry on.
"""

import secrets

from app.core.base62 import ALPHABET

DEFAULT_CODE_LENGTH = 7


def generate_short_code(length: int = DEFAULT_CODE_LENGTH) -> str:
    """
    Generate a cryptographically random Base62 string of the given length.

    We use `secrets` (not `random`) because:
      - `random` is a Mersenne Twister PRNG — statistically fine for
        simulations, but NOT safe where predictability has security
        implications (an attacker who can predict short codes could
        enumerate/guess other users' links).
      - `secrets` is drawn from the OS's cryptographically secure
        source and is the standard library's explicit recommendation
        for anything security-sensitive, including tokens and IDs
        exposed to the outside world.

    Collision probability at length=7 (62^7 ≈ 3.5 trillion possible
    codes): by the birthday-paradox approximation, you'd need roughly
    sqrt(62^7) ≈ 1.9 million existing codes before a 50% chance of any
    collision on the NEXT insert. For a portfolio-scale project, this
    means collisions are something we handle correctly, not something
    we expect to see in practice.
    """
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
