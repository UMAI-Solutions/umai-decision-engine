# ADR-0046 — API key network restrictions: allowed networks per key, the client address through trusted proxies only, failed credentials throttled per source

- **Status:** Accepted (2026-10-31)
- **Date:** 2026-10-30
- **Deciders:** engineering (proposed; the product owner accepts or amends)
- **Builds on:** ADR-0023 (per-tenant API keys, signed key-registry snapshots, admission, §7 abuse
  limits per credential), ADR-0032 (single VM: Caddy in front of the engine and the gateway),
  ADR-0034 / ADR-0039 (the gateway admits the same keys), ADR-0043 (the snapshot carries more than
  keys; admission refuses before anything is evaluated)
- **Closes:** gap analysis 6.1.1 / 6.3.1 (anti-brute-force per source) and 15.3.4 (client
  address from trusted headers only); the Cloud SaaS checklist items "IP restriction" and
  "rate limiting and abuse protection" for API keys

## Context

An API key works from any address. A key that leaks (a repository, a log, a laptop) is usable
from anywhere until someone notices and revokes it, and enterprise buyers ask for the usual
answer: "this key only works from our egress addresses". Admission also has no limit per source
on bad credentials: the per-key token bucket (ADR-0023 §7) only meters keys that authenticate, so
a caller can present wrong keys or a wrong operator token at whatever rate the ingress allows.
With 256-bit secrets guessing is hopeless, but it costs lookups and hashing, fills logs once
401s are logged (ADR-0030), and the standards ask for the control (ASVS 6.1.1 / 6.3.1).

Both need the caller's address. Behind a reverse proxy the socket peer is the proxy, and the
client is in `X-Forwarded-For`, which any caller can also send. Uvicorn has its own
`--proxy-headers`, on by default and trusting `127.0.0.1`, which rewrites the peer before the
application sees it.

Constraints: no database on the request path (ADR-0023 §4: snapshots); a refusal never becomes a
decision; admission must stay cheap (AGENTS rule 6); one release serves cloud and self-hosted
(ADR-0018), so the proxy setup is configuration, not code.

## Decision

1. **D1 — Allowed networks per key.** A key may carry `allowed_ips`: 1–50 IPv4 or IPv6
   networks in CIDR form (a bare address is one host). The policy service stores them in
   canonical form (`api_keys.allowed_ips`, migration 0013) and refuses host bits
   (`203.0.113.5/24`), zone indexes and duplicates with 422 `invalid_allowed_ips` naming the
   entry. They are set when the key is created and can be replaced or cleared at any time on an
   active key (`PATCH /v1/tenants/{id}/keys/{key_id}`, permission `key.update`: owner, admin,
   operator); every change republishes the tenant's signed snapshot, which carries them per entry
   (credentials 0.9, snapshot payload 0.5). Absent (or an empty list): any address, as before.
   At admission, after the credential is verified and before the per-key rate limit, scope,
   quota or anything else, a key used from an address outside its networks gets **403
   `ip_not_allowed`** — in the engine API and the gateway alike. An IPv4-mapped IPv6 address is
   compared as IPv4; a client whose address cannot be known is outside every network. The
   refusal is not counted as a bad credential (D3) and does not spend the key's rate budget, so a
   leaked key used elsewhere cannot exhaust the owner's limit or lock out the owner's network.
2. **D2 — The client address comes from trusted proxies only.** The client is the socket peer,
   unless the peer is in `UMAI_DE_TRUSTED_PROXIES` (CIDRs; **default empty: trust none**). Behind
   a trusted proxy `X-Forwarded-For` (all its lines, in order) is read from the right, skipping
   trusted hops; the first untrusted hop is the client (a chain of only trusted hops: its first).
   Entries a caller put to the left are never read. No other header is read (`Forwarded`,
   `X-Real-IP`). An unreadable hop where the client should be makes the client unknown. The
   images run uvicorn with `--no-proxy-headers`, so this setting is the only authority. On the
   single VM the Compose network has a fixed subnet (`172.30.240.0/24`), Caddy a fixed address
   (`172.30.240.2`) and the engine and the gateway trust exactly that address; Caddy itself
   ignores an incoming `X-Forwarded-For` from clients and sets the address it saw.
3. **D3 — Failed credentials are throttled per source.** A presented credential that
   authenticates nobody (unknown key id, wrong secret, revoked or expired key, wrong operator
   token, any other bearer value) takes one token from its source's bucket: `limit` tokens
   (`UMAI_DE_AUTH_FAILURE_LIMIT`, default 20) refilled over `UMAI_DE_AUTH_FAILURE_WINDOW_S`
   (default 60 s). An empty bucket refuses every credential from that source with **429
   `too_many_failed_attempts`** and `Retry-After` (the seconds until one token is back) **before
   any key lookup or hashing**. A source is an IPv4 address or an IPv6 /64 (one subscriber's
   usual allocation). A missing credential is not a guess and is not counted. A success never
   refills the bucket: otherwise one valid key would reset the count between guesses. In process,
   like the per-key bucket (ADR-0023 §7; Redis in the regional stack, ADR-0031 §7); the table keeps
   only sources that failed within the last window and at most `UMAI_DE_AUTH_FAILURE_MAX_SOURCES`
   (default 100 000), evicting the oldest beyond that. `UMAI_DE_AUTH_FAILURE_LIMIT` empty turns it
   off. The start of a lockout is logged once (`admission.throttled source=<address or /64>`);
   a presented credential is never logged.
4. **D4 — Order at admission:** credential present → source not throttled (429) → credential
   valid (401, counted) → key's networks (403) → per-key rate limit (429 `rate_limited`) → scope
   (403) → quota (429 `quota_exceeded`). A refused request is never evaluated, forwarded or
   counted toward the quota.
5. **D5 — Logs.** `ip_not_allowed` refusals log tenant, key id and the client address; the
   throttle logs the source when a lockout starts. An address is personal data, kept in the
   process log for security monitoring only (ADR-0030 plans the same `ip` field for audit
   events); no record table stores it.

## Alternatives considered

| Option | Why not |
|---|---|
| Allowed networks per tenant or per application | Coarser than needed: a CI key and an office key of one application have different sources; per key is the unit that is created, rotated and revoked. A tenant-wide list can be added later as a default |
| Answer a disallowed address with 401 (hide that the key is valid) | Only the holder of the full secret sees the 403; the key's owner needs to tell "wrong key" from "wrong network" to fix a client |
| Count `ip_not_allowed` as a failed credential | A misconfigured list would lock the whole office out for its other keys too |
| Reset a source's count on a successful authentication | One valid key would reset the count between guesses |
| Trust `X-Forwarded-For` from any private address, or its leftmost entry | Spoofable by any caller (leftmost) or by any container on the host network; the proxy is a known address |
| Rely on uvicorn's `--proxy-headers` / `--forwarded-allow-ips` | Its default trusts `127.0.0.1`, which a local caller could exploit, and it rewrites the peer invisibly; one setting the application reads and tests is clearer |
| Read RFC 7239 `Forwarded` | Caddy sends `X-Forwarded-For`; two headers means two parsers and a precedence rule to get wrong |
| Throttle in Caddy (rate-limit module) or a WAF | Caddy's rate limiting is a third-party module not in the official image; a WAF does not know which credentials failed. The SaaS edge (Application Gateway WAF, ADR-0031) adds coarse limits on top |
| Count failures per IPv6 address | A /64 holds 2⁶⁴ addresses: rotating within it would reset the count every time |

## Consequences

- Customers can pin a key to their egress networks in the console (Applications → application →
  Keys) and change the list without a new key; engines enforce a change within one poll
  interval (default 5 s).
- A snapshot written by this release (payload 0.5) is refused by an older engine, which keeps
  its last good keys for that tenant: roll engines and the gateway out before, or with, the
  policy service (on the single VM they are one image). Older snapshots (0.1–0.4) still load.
- The single VM's Compose network now has a fixed subnet. Docker Compose recreates an existing
  `umai_default` network with it on the next `umai.sh up` (verified with Compose 2.40); older
  Compose versions need `docker compose -p umai down` once (deploy/vm/README.md).
- Every deployment behind a proxy must set `UMAI_DE_TRUSTED_PROXIES`, or every caller appears as
  the proxy: allowed networks then refuse everyone, and the failure throttle counts all callers
  as one source. The VM's Compose file sets it; the SaaS regional stack sets its ingress
  addresses.
- NAT: callers behind one egress address share one failure bucket; a client looping on a revoked
  key can throttle its colleagues for up to a minute. The limit and window are settings.
- The policy service gains route `PATCH /v1/tenants/{id}/keys/{key_id}` and permission
  `key.update`.

## Validation

- Tests: allowed and refused addresses (IPv4, IPv6, IPv4-mapped, unknown), create / edit / clear
  through the policy service and the signed snapshot, trusted-proxy handling (rightmost untrusted
  hop, spoofed entries ignored, untrusted peers' headers ignored), throttling and its refill,
  IPv6 /64 sharing, the bounded table, snapshot compatibility (0.4 loads and verifies), the
  gateway path (`tests/unit/test_key_networks.py`, `tests/integration/test_key_networks.py`).
- Benchmark `python -m evals.run --suite admission` (2026-10-30, laptop,
  `evals/reports/2026-10-30-admission-key-networks`): admission was 15.9 µs per request; with
  the client address and the throttle 21.3 µs (+5.4), behind a trusted proxy 27.8 µs (+11.9),
  with 50 networks 32.4 µs (+16.5); a throttled source costs 11.8 µs, less than before. Against a
  gateway call of ~10 ms p50 (gateway-latency) that is ≤ 0.2 %. Networks are integer
  (network, mask) pairs and addresses are parsed with `inet_pton`, a fifth of `ipaddress`' cost.
- Revisit when the regional stack moves buckets to Redis (both buckets move together), if
  customers need more than 50 networks per key, or if NAT-shared throttling shows up in support.
