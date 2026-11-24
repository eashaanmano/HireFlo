# Google Sign-in and NITC-only Addresses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Continue with Google" for registered `@nitc.ac.in` users, and an institute-only e-mail rule on the login page and the server.

**Architecture:** A server-side OpenID Connect authorization-code flow (state + nonce + PKCE in a signed, short-lived
cookie) in a new router; ID tokens verified against Google's keys in a small service; the existing session cookie is
reused. The login page asks `/api/auth/options` whether Google is on and which domain to enforce.

**Tech Stack:** FastAPI, PyJWT with `cryptography` (RS256 + `PyJWKClient`), httpx, React + Vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-google-sign-in-design.md`

## Global Constraints
- Settings: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `INSTITUTE_DOMAIN` (default `nitc.ac.in`, blank = any domain).
- Google is on only when both keys are set; otherwise `/api/auth/google/*` return 404 and no button is shown.
- Scope `openid email profile`; auth URL params include `hd=<domain>`, `prompt=select_account`, `code_challenge_method=S256`.
- Redirect URI = `PUBLIC_URL` + `/api/auth/google/callback`. Flow cookie lives 10 minutes, path `/api/auth/google`.
- Failure redirects: `/login?error=` one of `cancelled`, `not_nitc`, `not_registered`, `expired`, `failed`.
- OTP refusal copy: `Use your @nitc.ac.in address.` (domain from settings). Login flag copy: `Only @nitc.ac.in addresses can sign in.`
- Secrets never committed; tests blank `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` in `conftest.py`.

## Review Focus
1. Google returns the e-mail in mixed case → still matches the stored lower-case user (test in Task 2).
2. The callback URL is opened twice (Back button) → second visit redirects with `expired`, never a 500 (Task 2).
3. Google's token endpoint or key endpoint is down → `failed` redirect, not a 500 (Task 2).
4. Typed address with spaces or capitals (` Name@NITC.AC.IN `) → not flagged (Task 3).
5. `/api/auth/options` unreachable → login page still works with OTP and no flag; the server still enforces (Task 3).

---

### Task 1: Institute-only OTP and settings

**Files:**
- Modify: `backend/app/core/config.py` (settings + `allows_email`), `backend/app/routers/auth.py` (domain check, `set_session_cookie`, `GET /options`), `backend/tests/conftest.py`
- Test: `backend/tests/test_google_auth.py`

**Interfaces:**
- Produces: `Settings.google_client_id: str`, `Settings.google_client_secret: str`, `Settings.institute_domain: str`,
  `Settings.google_enabled -> bool`, `Settings.allows_email(email: str) -> bool`;
  `auth.set_session_cookie(response: Response, user: User) -> None`; `GET /api/auth/options -> {"google": bool, "domain": str}`.

- [ ] **Step 1: Failing tests** — `test_options_report_google_off_without_keys` (`{"google": False, "domain": "nitc.ac.in"}`);
  `test_otp_refuses_addresses_outside_the_institute` (request and verify with `someone@gmail.com` → 400, detail
  `Use your @nitc.ac.in address.`); `test_blank_institute_domain_allows_any` (`Settings(institute_domain="").allows_email("a@b.com")`).
- [ ] **Step 2:** `pytest tests/test_google_auth.py -q` → FAIL.
- [ ] **Step 3:** Implement; `allows_email` lower-cases and strips, compares `endswith("@" + domain)`.
- [ ] **Step 4:** Tests pass; full `pytest -q` still green.

### Task 2: Google OpenID Connect flow

**Files:**
- Create: `backend/app/services/google_oidc.py`, `backend/app/routers/google_auth.py`
- Modify: `backend/app/core/security.py` (flow token), `backend/app/main.py` (include router), `backend/requirements.txt` (`pyjwt[crypto]>=2.8`)
- Test: `backend/tests/test_google_auth.py`

**Interfaces:**
- Consumes: Task 1 settings, `set_session_cookie`.
- Produces: `google_oidc.AUTH_URL`, `TOKEN_URL`, `JWKS_URL`; `authorization_url(settings, *, redirect_uri, state, nonce, code_challenge) -> str`;
  `exchange_code(settings, *, code, code_verifier, redirect_uri) -> str` (the ID token; calls `_post_token(data) -> dict`);
  `verify_id_token(settings, token, *, nonce) -> GoogleIdentity(email, name, sub)` (key from `_signing_key(token)`);
  `GoogleSignInError(reason)`; `security.create_google_flow_token(state, nonce, verifier, next_path) -> str`,
  `decode_google_flow_token(token) -> dict`; `GOOGLE_FLOW_COOKIE = "driveops_google"`; `safe_next(path) -> str`.

- [ ] **Step 1: Failing tests** (RSA key generated in the test; `_signing_key` and `_post_token` monkeypatched):
  `test_start_redirects_to_google` (302 to `AUTH_URL`, params per Global Constraints, flow cookie set);
  `test_callback_signs_in_a_registered_nitc_user` (next `/student/offers` → Location `/student/offers`, `/api/auth/me` works, `auth.login` event with `{"method": "google"}`, flow cookie cleared);
  `test_google_email_in_mixed_case_still_matches`;
  `test_callback_rejects` parametrized → reason: no/other `hd` → `not_nitc`; `email_verified` false → `not_nitc`; unknown or inactive user → `not_registered`; wrong `aud`/`iss`/`nonce`, expired, other key → `failed`; token endpoint raises → `failed`;
  `test_state_mismatch_missing_cookie_or_reuse_is_expired`; `test_cancelled_at_google` (`error=access_denied` → `cancelled`);
  `test_unsafe_next_goes_home` (`//evil.example`, `https://evil.example` → `/`); `test_google_routes_404_when_off`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Accept `iss` in `{"https://accounts.google.com", "accounts.google.com"}`; require `hd == domain` when a domain is set; `PyJWKClient(JWKS_URL, cache_keys=True)` created once; httpx timeout 10 s; any `httpx`/`jwt` error → `GoogleSignInError("failed")`.
- [ ] **Step 4:** Tests pass; `ruff check app tests` and `mypy app` clean.

### Task 3: Login page

**Files:**
- Create: `frontend/src/lib/email.ts`, `frontend/src/lib/email.test.ts`
- Modify: `frontend/src/pages/Login.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `GET /api/auth/options`, `/api/auth/google/start?next=`, `/login?error=`.
- Produces: `domainProblem(email: string, domain: string, submitted: boolean): string | null`.

- [ ] **Step 1: Failing tests** — `""`/no `@` → null; `x@ni` → null while typing; `x@g` → flag; `x@nitc` → null typing, flag when submitted; ` X@NITC.AC.IN ` → null; domain `""` → null.
- [ ] **Step 2:** `npm test` → FAIL.
- [ ] **Step 3:** Implement; Login shows the Google button (Google "G" mark, "Continue with Google") and an "or" divider only when `options.google`; flag under the field, "Send code" disabled while flagged; `?error=` mapped to a message.
- [ ] **Step 4:** `npm test`, `npm run build`, `npx tsc --noEmit` pass.

### Task 4: Deployment and docs

**Files:** `render.yaml` (comment list), `backend/.env.example`, `docs/DEPLOY.md` (Google section), `README.md` (roles table, config rows, security list), `docs/architecture.md` (session nodes).

- [ ] Document the Google client setup (three redirect URIs), the two keys for Render and `.env`, and the NITC-only rule.
- [ ] Commit tasks 1–4; push `deploy/render` and `main`; the presenter adds the keys on Render and in `backend/.env`.
- [ ] Verify: local sign-in through Vite (:5173), live sign-in on Render, non-NITC flag on both.
