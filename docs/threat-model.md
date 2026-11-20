# Threat model

## Assets
- **Cryptographic Keys & Secrets:** `JWT_PRIVATE_KEY` (RS256 signing key), `COOKIE_SECRET` (HMAC cookie/CSRF secret), `HCAPTCHA_SECRET`, `SLACK_WEBHOOK_URL`, and database credentials (`MONGO_URI`, `POSTGRES_PASSWORD`).
- **User Credentials & Sessions:** Argon2id password hashes in MongoDB `users`, active JWT access tokens, refresh session tokens, and Redis JTI revocation lists (`revoked_jti:*`).
- **PII & Audit Trails:** User email addresses, IP addresses, device fingerprints, and `feature_flag_audits` change history.
- **Compute & Downstream Capacity:** Event-loop availability, Argon2id CPU worker pool, PostgreSQL/MongoDB/Redis connection pools, and outbound third-party API quotas.

## Entry points
- **REST HTTP API:** Public (`@public`) and authenticated routes, including JSON bodies, query parameters, headers (`X-Request-Id`, `X-Request-Timeout-Ms`, `X-CSRF-Token`, `X-Forwarded-For`), and cookies (`access_token`, `session_id`, `csrf_token`, `guest_id`).
- **Multipart File Upload Endpoint (`POST /uploads`):** Raw binary streams and filenames/MIME headers.
- **GraphQL Endpoint (`POST /graphql`):** Arbitrary nested AST queries and variables.
- **Socket.IO WebSocket Endpoint (`/socket.io` -> `/realtime`):** Handshake headers/cookies/auth payload and inbound event frames (`join`, `message`, `admin-broadcast`).
- **Outbound HTTP Client:** Dynamic target URLs and upstream response payloads.

## Trust boundaries
1. **Internet Client <-> AWS ALB / Edge Proxy:** Untrusted network traffic; `X-Forwarded-For` is only trusted when `TRUST_PROXY` is explicitly enabled or matches the proxy CIDR.
2. **ASGI Middleware Perimeter <-> Domain Handlers:** Unauthenticated, unsanitized traffic crosses into authenticated, rate-limited, geo-checked, and sanitized domain logic.
3. **Application Process <-> Backing Stores (Postgres, Mongo, Redis, ClamAV):** Internal network boundary with strict connection timeouts and parameterized queries.
4. **Application Process <-> External Third-Party APIs (hCaptcha, Slack, Outbound HTTP targets):** Outbound calls traverse SSRF validation, circuit breakers, and deadline propagation.

## Abuse cases
- **CPU Exhaustion via Login Flood:** Attacker floods `/auth/login` or `/auth/register` to saturate CPU with Argon2id hashing.
- **Cross-Site Request Forgery (CSRF):** Malicious site triggers state-changing requests using browser-attached `access_token` or `session_id` cookies.
- **Server-Side Request Forgery (SSRF):** Attacker supplies internal IP/DNS (`169.254.169.254`, `127.0.0.1`, RFC1918) to an outbound HTTP fetch.
- **GraphQL Denial of Service:** Deeply nested queries or high-cost field amplification exhaust memory/DB connections.
- **Malicious File Upload:** Attacker uploads an executable disguised with a `.png` extension or embeds malware.
- **Cache Stampede / Thundering Herd:** Hot cache key expires under high concurrency, causing hundreds of simultaneous database queries.
- **WebSocket Connection / Message Flood:** Single user opens thousands of sockets or floods events to starve Redis pub/sub.
- **Stored XSS & Log Injection / PII Leak:** HTML payloads submitted in strings or sensitive tokens leaked into application logs.

## Mitigations
- **Argon2id Concurrency Bulkhead:** Bounded semaphore + queue (`AUTH_PASSWORD_HASH_MAX_CONCURRENT=2`, `MAX_QUEUE=32`, `MAX_WAIT_MS=2000`) sheds excess password hashing with HTTP `503`.
- **Double-Submit HMAC CSRF:** `CsrfMiddleware` enforces matching HMAC-signed `csrf_token` cookie and `X-CSRF-Token` header on all non-exempt state-changing methods (`POST`, `PUT`, `PATCH`, `DELETE`).
- **Outbound SSRF Policy:** `HttpClient` resolves DNS and blocks loopback, link-local, and RFC1918 private IPs unless `HTTP_CLIENT_ALLOW_PRIVATE_URLS=true`.
- **GraphQL Depth, Complexity & Concurrency Budgets:** `app/graphql/query_limits.py` enforces max depth `10` and per-query `GQL_MAX_COMPLEXITY`; `GraphqlProtectionExtension` applies a 60s Redis rolling budget (`GQL_USER_BUDGET=1000`) and active execution lease (`GQL_MAX_CONCURRENCY=3`, TTL `GQL_CONCURRENCY_KEY_TTL_SEC`).
- **GeoIP Country Access:** `CountryAccessDependency` runs after JWT and before rate limiting; PostgreSQL rules refresh on an interval and MaxMind GeoLite2 resolves client country (bypass paths and `@skip_country_access` respected). Client IP comes from `resolve_client_ip` / `TRUST_PROXY`: when disabled, only the socket peer is used; when set to CIDRs, `X-Forwarded-For` is honored only from a trusted immediate peer with spoofed hops stripped from the right of the chain.
- **Production Surface Reduction:** `NODE_ENV=production` omits `/example/*` demo routes (including unauthenticated job enqueue) and fails fast when `ALLOWED_ORIGINS` is missing or wildcard for REST CORS (WebSocket already guarded).
- **Feature-Flag Admin Authorization:** Admin mutations re-check MongoDB role and `FEATURE_FLAG_ADMIN_EMAILS` on each request (stale JWT `admin` claims alone are insufficient).
- **Multipart Magic-Byte & ClamAV Scanning:** Strict stream size limits (`UPLOAD_MAX_FILES=5`, `UPLOAD_MAX_FILE_SIZE=5MB`, `UPLOAD_MAX_TOTAL_SIZE=20MB`), magic-byte signature verification (`filetype`), and ClamAV TCP `INSTREAM` scan (`UPLOAD_VIRUS_SCAN=true`).
- **SWR + Single-Flight + Distributed Redis Lock:** Cache layer coalesces in-process misses and acquires Redis `SET NX PX` loader locks with TTL jitter and negative caching.
- **WebSocket Handshake & Rate Guards:** Redis-backed connection cap (`WEBSOCKET_MAX_CONNECTIONS_PER_USER=5`), message rate limit (`120/min`), and strict room isolation (`user:<id>`).
- **Recursive Input Sanitization & PII Log Redaction:** Pre-validation HTML tag stripping (`nh3`) on string inputs and recursive sensitive key/pattern masking in `structlog` processors.
