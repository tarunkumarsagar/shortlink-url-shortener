# ADR-005: URL Shortening Algorithm

## Context
Need to generate a short, unique code for every URL, safely under concurrent requests from multiple backend instances.

## Options Considered
- **Auto-increment ID + Base62 encoding**: sequential DB counter encoded into a short alphanumeric string. Zero collision probability by construction, but sequential codes are enumerable (a real information leak), and a single shared counter is a write-contention point across multiple instances.
- **Random string generation (chosen)**: cryptographically random Base62 string, uniqueness enforced by a DB constraint with retry-on-collision.
- **Hash-based (MD5/SHA of the long URL, truncated)**: looks elegant (free dedup for identical URLs) but breaks the moment two different users/contexts want the same URL with different ownership or expiration -- per-user salting would be needed anyway, erasing the benefit. Truncated hashes also have materially higher collision odds than the full hash space.
- **UUID**: globally unique, zero coordination, but 36 characters defeats the point of "shortening."
- **Snowflake-style ID**: solves the shared-counter bottleneck elegantly, but solves a distributed-ID-generation-at-massive-scale problem this project doesn't have yet.

## Decision
Cryptographically random Base62 string (via Python's secrets module, not random), 7 characters by default, with a DB UNIQUE constraint on urls.short_code and retry-on-collision in the service layer.

## Reason
- Non-enumerable: nobody can guess "the next" code or scrape the URL space, unlike a sequential ID -- a real security/privacy property.
- No shared counter: multiple backend instances generate codes independently with zero write contention on a single sequence, which matters for the project's horizontal-scaling goal.
- secrets over random: random is a Mersenne Twister PRNG, fine for simulations but not safe where predictability has security implications (an attacker who could predict codes could enumerate other users' links). secrets draws from the OS's cryptographically secure source.

## Collision Math
At length 7, the code space is 62^7 ~= 3.5 trillion. By the birthday-paradox approximation, roughly 1.9 million existing codes would need to exist before a 50% chance of any collision on the next insert -- far beyond this project's realistic scale, and even then, collisions are handled correctly (detected via the UNIQUE constraint, retried with a new random code up to 5 times) rather than assumed away.

## Trade-offs
A (very small) chance of collision on generation, requiring a retry loop, versus Base62-of-sequential-ID's zero-collision guarantee. Accepted for the security/scaling benefits above.

## Future
Base62 encoding of the primary key is implemented and tested as a documented alternative strategy (app/core/base62.py) -- not used as the primary generation path, but available and understood as the trade-off comparison this ADR describes.
