# ADR-007: Rate Limiting Strategy

## Context
Public URL creation (including anonymous) needs protection from abuse without adding a hard dependency that could take down the whole API if it fails.

## Options Considered
- **Fixed window**: simplest, but allows a 2x burst at window boundaries (e.g. 20 requests at 0:59, another 20 at 1:00 -- effectively 40 in 2 seconds).
- **Sliding window (chosen)**: continuously moving lookback, no boundary burst exploit.
- **Token bucket**: allows controlled bursting with a steady refill rate -- a legitimate alternative, more complex to reason about for this project's needs.
- **Leaky bucket**: smooths bursts into a constant output rate -- better suited to traffic shaping than request admission control.

## Decision
Sliding window via Redis sorted sets: for each identity, remove entries older than the 60-second window, count what remains, admit the request only if under the limit.

## Reason
Sliding window avoids the fixed-window boundary-burst problem with a straightforward implementation using Redis's native sorted-set operations (ZREMRANGEBYSCORE, ZCARD, ZADD). Redis-backed (not in-process) because the limit must hold correctly across multiple backend instances; an in-process counter would let each instance independently grant its own full quota, multiplying the effective limit by the instance count.

## Identity and Limits
- Authenticated requests: keyed by user id (stable identity, fair even behind shared IPs/NAT), higher limit.
- Anonymous requests: keyed by client IP, lower limit -- the best available identity without requiring login, with the known, accepted weakness that many users sharing one IP share one quota.

## Trade-offs
Fails open on Redis errors: an outage disables rate limiting rather than blocking all traffic. A rate limiter that fails closed would turn a caching-layer outage into a full API outage, a strictly worse failure mode.

## Future
Per-endpoint limits (distinct limits for creation vs. redirect vs. analytics) are a natural extension once real usage patterns are observed; today only URL creation is rate-limited, as the highest-risk-of-abuse write path.
