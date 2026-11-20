# Security Audit Report — FastAPI Enterprise Backend Boilerplate

**Date:** 2026-11-21  
**Scope:** Repository-wide (`app/`, `Dockerfile`, CI)  
**Auditor:** Composer (`composer-2.5-fast`) — full phased re-audit with disprover gate  
**Artifacts:** [`coverage-ledger.json`](./coverage-ledger.json), [`findings.json`](./findings.json)

---

## 1. Executive Verdict

**FAIL (ACTION REQUIRED)**

One **Medium** finding (**SEC-AUD-011**) is confirmed: country access can be bypassed by forging `X-Forwarded-For` because the guard does not gate on `TRUST_PROXY` as documented in `docs/threat-model.md`. Prior same-day findings **SEC-AUD-001** through **SEC-AUD-005** were **rejected** after code verification (GraphQL limits, country dependency, production CORS, feature-flag live admin check are implemented).

---

## 2. Coverage Ledger Summary

| Metric | Value |
|--------|------:|
| Units mapped | 19 |
| Units audited | 19 |
| Coverage | 100% |

Surfaces covered: REST (`/auth`, `/health`, `/feature-flags`, `/uploads`, `/example/*`), GraphQL (`/graphql`), Socket.IO (`/socket.io`), conformance harness (`/conformance/*`), outbound HTTP (Slack, hCaptcha), background jobs, config/CI, container runtime (`Dockerfile`), and data access (Mongo, Postgres, Redis).

---

## 3. Confirmed Findings

### SEC-AUD-011 — Country access trusts client `X-Forwarded-For` without `TRUST_PROXY` (Medium)

- **Location:** `app/common/country_access/country_access_guard.py:74-79`
- **Threat:** CWE-290 spoofing of client identity; documented trust boundary violated.
- **Exploit:** With active deny rules, attacker sends `X-Forwarded-For: <IP in allowed region>`; guard prefers header over `request.client` and never reads `app.state.trust_proxy`.
- **Disprover:** Searched for `trust_proxy` usage outside config — not referenced in country access path. Inactive service and bypass paths do not negate active-rule bypass.
- **Remediation:** Implement trusted-proxy parsing (only honor XFF when immediate peer is trusted); align with NestJS reference `trust_proxy` helper; add tests for `TRUST_PROXY=false` ignoring client XFF.

---

## 4. Needs Validation (runtime / ops)

| ID | Topic | Why |
|----|--------|-----|
| SEC-AUD-012 | `Dockerfile` `--forwarded-allow-ips=*` | Safe only if port 3000 is never reachable except via an overwriting edge proxy |
| SEC-AUD-013 | WebSocket `admin-broadcast` JWT role only | Stale-admin window; parity gap vs REST feature-flag live check |
| SEC-AUD-006 | Redis rate-limit memory fallback | Multi-instance / Redis SLO dependent |
| SEC-AUD-007 | `UPLOAD_VIRUS_SCAN` default false | Ops configuration when `/uploads` is exposed |

---

## 5. Rejected Candidates (audit trail)

| ID | Title | Reason |
|----|--------|--------|
| SEC-AUD-001 | GraphQL budgets stub | `GraphqlProtectionExtension` fully implemented |
| SEC-AUD-002 | Public job enqueue | Example routers excluded in production |
| SEC-AUD-003 | Country guard not mounted | `CountryAccessDependency` on global router deps |
| SEC-AUD-004 | REST CORS wildcard in prod | `assert_rest_production_origins` fail-fast |
| SEC-AUD-005 | Feature-flag JWT role only | `assert_feature_flag_admin` live DB/allowlist |
| SEC-AUD-008 | Conformance loopback SSRF | Blocked when `NODE_ENV=production` |
| SEC-AUD-009 | CSRF skipped for Bearer | Intentional API-client mode |
| SEC-AUD-010 | HTTP private URL flag | Default denies private IPs |

---

## 6. Threat Model Updates Required

Update `docs/threat-model.md` to reflect **current** runtime posture:

1. **Country access:** Document that `_resolve_client_ip` **always** honors client `X-Forwarded-For` today (gap vs trust-boundary text); remove “not wired” note if present from earlier audits.
2. **GraphQL:** State that depth/complexity/Redis budgets **are enforced** via `GraphqlProtectionExtension`.
3. **Example module:** `/example/*` is **non-production only** (`NODE_ENV != production`); unauthenticated job enqueue remains a dev/staging concern.
4. **Container:** Document `forwarded-allow-ips=*` assumption and required network isolation.

---

## 7. Positive Controls Observed

- Default-deny JWT guard with `@public` opt-in; RS256 with explicit algorithms.
- Double-submit CSRF for cookie sessions; Bearer clients exempt by design.
- Outbound SSRF DNS/IP checks in `ConfiguredHttpClient` (default deny private).
- Conformance harness refused in production via `validate_env`.
- Production REST CORS and WebSocket origin guards.
- Feature-flag admin live DB + email allowlist on REST routes.
- Auth password hashing bulkhead, upload magic-byte validation, Slack webhook host pinning.
- CI: pinned actions, gitleaks, semgrep, pip-audit.

---

## Next Steps

1. Human owner triages `findings.json` (do not set Status to `accepted` in this pass).
2. Dispatch `security-implementer` for **SEC-AUD-011** after acceptance.
3. Re-run `/security-audit` after trusted-proxy IP resolution ships.