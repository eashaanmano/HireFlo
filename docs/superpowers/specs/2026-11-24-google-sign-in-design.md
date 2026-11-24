# Google sign-in (OAuth 2.0 / OpenID Connect) and NITC-only addresses

Short design, 2026-11-24.

## Why
The presenter wants to show evaluators a real third-party single sign-on: "Continue with Google" with the institute
account `eashaan_m260351cs@nitc.ac.in`, on the hosted site and on a local run. `nitc.ac.in` mail is Google Workspace
(its MX records are Google's), so that address already is a Google account and Google is the institute's own identity
provider. Typing an address outside `@nitc.ac.in` should be flagged straight away.

## Goals
- A "Continue with Google" button on the login page that signs a registered user in with their NITC Google account.
- Only `@nitc.ac.in` accounts get in, by Google sign-in or by OTP; the login page flags any other domain as it is typed.
- Works on https://hireflo-8w3b.onrender.com and locally (Vite on :5173, or the container on :8000).
- The deployment (Render settings, `render.yaml`, docs, README, `.env.example`) is updated to match.

## Non-goals
- No new accounts from Google sign-in: only users already in HireFlo (the seeded season) can sign in.
- No Google sign-in for recruiters or panels (they keep their links). No Google API access, no stored Google tokens.

## Anti-goals
- E-mail OTP and the one-click demo accounts must keep working exactly as now.
- Without Google keys configured the login page looks as it does today (no dead button).

## Approaches
| | Approach | Trade-off |
|---|---|---|
| **Chosen** | **Google directly** (OpenID Connect, authorization-code flow, server side) | Free with no user cap, the institute's real identity provider, no extra vendor; evaluators see Google rather than a separate auth brand. |
| | Auth0 (free tier) as a broker in front of Google | A named identity platform in the architecture, but one more account and dashboard, an extra redirect, and its built-in Google keys are for testing only. |
| | Clerk / WorkOS | Front-end SDKs that bring their own sessions; would duplicate HireFlo's existing session cookie. |

The presenter picked Google directly.

## Design
- **Settings:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (secret; pasted into `.env` and Render, never committed) and
  `INSTITUTE_DOMAIN` (default `nitc.ac.in`; blank allows any domain). Google sign-in is on only when both keys are set.
- **`GET /api/auth/options`** tells the login page whether Google is on and which domain to enforce.
- **`GET /api/auth/google/start?next=…`** creates `state`, `nonce` and a PKCE verifier, keeps them in a signed,
  HttpOnly, 10-minute cookie scoped to `/api/auth/google`, and redirects to Google with `scope=openid email profile`,
  `hd=nitc.ac.in` and `prompt=select_account`.
- **`GET /api/auth/google/callback`** checks `state` against the cookie, exchanges the code (client secret + PKCE
  verifier) at Google's token endpoint, and verifies the ID token against Google's published keys: signature, `iss`,
  `aud`, `exp`, `nonce`, `email_verified`, and `hd` equal to the institute domain (Google's docs say `hd` is the claim
  to trust, not the e-mail's domain). It then finds the active user by e-mail, sets the same session cookie as OTP,
  records an `auth.login` event with `method: google`, and redirects to `next` (relative paths only) or `/`.
  Any failure redirects to `/login?error=<reason>` (`cancelled`, `not_nitc`, `not_registered`, `expired`, `failed`)
  and the login page explains it.
- **Redirect URI** = `PUBLIC_URL` + `/api/auth/google/callback`; the Google client lists the Render URL,
  `http://localhost:5173` and `http://localhost:8000` versions.
- **Domain rule on the server:** `/otp/request` and `/otp/verify` refuse other domains with a clear 400
  ("Use your @nitc.ac.in address"). This reveals nothing about who is registered.
- **Login page:** "Continue with Google" above the e-mail form with an "or" divider. As soon as the text after `@`
  stops matching the start of `nitc.ac.in`, a warning appears under the field and "Send code" is disabled; a
  submitted address that is not complete (`…@nitc`) gets the same warning.
- **Library:** `pyjwt[crypto]` (adds `cryptography` for RS256); `httpx` already does the token exchange.
- **Tests:** a locally generated RSA key stands in for Google's keys and the token endpoint is faked: happy path,
  wrong `hd`, unverified e-mail, unknown user, bad `state`, reused/expired cookie, unsafe `next`, Google off;
  OTP domain refusal; the login page's domain flag.

## Left to the builder
Exact copy, button styling (Google's branding rules for the "G" mark), and where the docs sections sit.

## Known risk
NITC's Workspace admin could block unconfigured third-party apps for students (`admin_policy_enforced`). The first
real sign-in will show it; the fallback is the OTP route, which is unchanged.
