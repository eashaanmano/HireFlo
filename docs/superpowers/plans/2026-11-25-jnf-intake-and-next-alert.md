# JNF intake and "you're up next" alert: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Companies fill a Job Notification Form through a signed link and the cell approves it into a drive;
students get one "you're up next" alert per interview.

**Architecture:** A new `job_notifications` table (created by `create_all()` at startup, so no reseed) and a signed
`jnf` link token, following the recruiter-link pattern; public endpoints under `/api/jnf`, cell endpoints under
`/api/cell/jnf`; a `jnf` inbox item. The alert is `live.call_next()`, deduped by a `slot.called` event, called from
the monitor, after an interview is marked done and after a re-plan is approved.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Pydantic v2, pytest; React 18, TypeScript, TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-jnf-intake-and-next-alert-design.md`

## Global Constraints

- No change to existing tables, the seed or demo data; only a new table and new event types.
- Commits carry no co-author or "Generated with" lines.
- Alert thresholds: `call_ahead_min = 15`, `call_ahead_count = 2`, 60-minute cap; link life `jnf_link_days = 30`;
  variable-pay flag at 25%.
- Public JNF endpoints return counts only, never student data; rate-limited like recruiter links.
- Copy is plain and specific, matching the product's voice.

## Review Focus

- A slot whose interview is tomorrow (multi-day round) with nobody ahead today must not be called.
- Calling twice (monitor tick, then panel Done) must not send a second alert for the same slot.
- A JNF link after approval or decline must still open (read-only) but refuse edits with a clear message.
- An HR typing an existing company's name in another case ("amd") must not create a duplicate company.
- A recruiter or panel token sent to `/api/jnf` must be refused (wrong token kind).

---

### Task 1: "You're up next" alert

**Files:**
- Modify: `backend/app/core/config.py` (add `call_ahead_min: int = 15`, `call_ahead_count: int = 2`)
- Modify: `backend/app/services/live.py` (add `call_next`; call it in `complete_slot`, `apply_proposal`, `monitor_tick`)
- Test: `backend/tests/test_call_next.py`

**Interfaces:**
- Produces: `call_next(db: Session, drive: Drive, now: datetime | None = None) -> int` (number of students alerted);
  event `slot.called` (entity `slot`, payload `{"ahead": int, "eta": iso}`).

- [ ] **Step 1: Write the failing tests**
  - `test_calls_students_close_to_their_turn_once`: 2 panels, 6 students scheduled from now; first call alerts
    exactly the waiting slots with ahead ≤ 2 or start ≤ 15 min; second call returns 0; one `slot.called` event per
    alerted slot; notification title contains "you're up".
  - `test_does_not_call_far_future_slot`: one student scheduled 3 h ahead with nobody ahead → 0 alerts.
  - `test_panel_done_calls_the_next_student`: through `/api/p/slots/{id}/complete`, the student behind gets a
    notification whose body names the room.
  - `test_not_checked_in_is_told_to_check_in`: body contains "Check in".
- [ ] **Step 2: Run** `pytest tests/test_call_next.py -q` → FAIL (`call_next` missing)
- [ ] **Step 3: Implement `call_next`** using `drive_projection`; ahead = count of projections with smaller
  position (same as the student banner); skip slots with an existing `slot.called` event; `notify` + `send_email`.
- [ ] **Step 4: Run** the new tests and `pytest -q` → all pass
- [ ] **Step 5: Commit** "Alert students shortly before their interview"

### Task 2: JNF storage, link and public form API

**Files:**
- Modify: `backend/app/models.py` (add `JobNotification`, table `job_notifications`)
- Modify: `backend/app/core/security.py` (token kind `jnf`; `create_jnf_token(jnf_id: int, version: int) -> str`,
  `decode_jnf_token(token: str) -> tuple[int, int]`)
- Modify: `backend/app/core/config.py` (`jnf_link_days: int = 30`)
- Modify: `backend/app/core/deps.py` (`JnfCtx` dependency: header `X-Access-Token` or `?token`)
- Modify: `backend/app/schemas.py` (`JnfInviteIn`, `JnfRound`, `JnfForm`, `JnfPoolIn`, `JnfNoteIn`, `JnfApproveIn`)
- Create: `backend/app/services/jnf.py` (form helpers, pool count, review flags, approval)
- Create: `backend/app/routers/jnf.py` (public `/api/jnf`) and register it in `backend/app/main.py`
- Test: `backend/tests/test_jnf.py`

**Interfaces:**
- Produces: `JobNotification` fields `id, company_name, contact_name, contact_email, company_id, status,
  link_version, data: dict, cell_note, drive_id, invited_by, decided_by, submitted_at, decided_at` +
  `created_at/updated_at`; statuses `invited | submitted | changes_requested | approved | declined`.
- Produces: `jnf.pool_count(db, form: JnfPoolIn) -> dict` → `{"eligible": int, "total_students": int, "tier": str}`;
  `jnf.review(db, n: JobNotification) -> dict` → `{"tier", "eligible", "total_students", "flags": list[str]}`;
  `jnf.approve(db, n, actor: str, open_now: bool) -> Drive`; `jnf.link_url(n) -> str` (`{public_url}/j/{token}`).
- Endpoints: `GET /api/jnf` → `{id, status, company_name, contact_name, data, cell_note, editable, options:
  {branches, programmes}}`; `POST /api/jnf/pool-preview`; `POST /api/jnf/submit` (409 when locked).

- [ ] **Step 1: Failing tests:** link opens and shows `invited` + branch/programme options; recruiter token →
  401; submit stores the form and sets `submitted`; pool preview counts by CGPA/branch and names the tier;
  approved/declined → GET works, submit → 409 "already approved"/"declined"; rotated link → 401.
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** model, token, dependency, schemas, service helpers and router.
- [ ] **Step 4: Run** → PASS, full suite green
- [ ] **Step 5: Commit** "Job notification form behind a signed company link"

### Task 3: Cell review, approval and inbox

**Files:**
- Create: `backend/app/routers/cell_jnf.py` (`/api/cell/jnf`), register in `main.py`
- Modify: `backend/app/services/insights.py` (`exceptions_inbox` adds kind `jnf`, `drive_id` 0)
- Test: `backend/tests/test_jnf.py`

**Interfaces:**
- Endpoints: `POST /api/cell/jnf` (invite → `{id, url, emailed}`), `GET /api/cell/jnf` (list), `GET
  /api/cell/jnf/{id}` (`{jnf, review, url}`), `POST /{id}/approve` (`{open_now}` → `{drive_id, opened,
  recruiter_url}`), `POST /{id}/request-changes` and `/{id}/decline` (`{note}`), `POST /{id}/rotate` (`{url}`).

- [ ] **Step 1: Failing tests:** student → 403; invite → link; submitted JNF in inbox as kind `jnf`; review flags
  (bond, variable ≥ 25%, breakup mismatch, same-date clash, too few eligible); approve creates company (or reuses
  "AMD" for "amd"), drive, rounds, approved rules, opens and notifies eligible students, recruiter link works;
  approve with JD-only extra rules keeps it a draft; request changes → HR can resubmit; decline locks; archived
  company blocks approval with 409.
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** router and inbox item.
- [ ] **Step 4: Run** → PASS; `ruff check app tests`
- [ ] **Step 5: Commit** "Cell reviews job notifications and approves them into drives"

### Task 4: Frontend

**Files:**
- Modify: `frontend/src/api/types.ts` (`Jnf*` types; `InboxItem.kind` adds `"jnf"`)
- Create: `frontend/src/lib/jnf.ts` (`blankJnf()`, `jnfPayload(form)`), test `frontend/src/lib/jnf.test.ts`
- Create: `frontend/src/pages/recruiter/JobNotificationForm.tsx` (route `/j/:token`)
- Create: `frontend/src/pages/cell/JnfReview.tsx` (route `/cell/jnf/:id`)
- Modify: `frontend/src/App.tsx`, `frontend/src/pages/cell/Companies.tsx` (invite modal + list),
  `frontend/src/pages/cell/Inbox.tsx` (label "Company form", action "Review")

- [ ] **Step 1: Failing test** `jnfPayload` turns empty numbers into `null`, upper-cases branches, keeps rounds
  in order.
- [ ] **Step 2–4:** implement pages; `npm test`, `npm run typecheck`, `npm run build` pass.
- [ ] **Step 5: Commit** "Job notification form, review page and inbox entry"

### Task 5: Docs, full check, ship

- [ ] README (recruiter row, highlights), `docs/architecture.md` (link kinds), then backend `pytest`, `ruff`,
  frontend `npm test`, `typecheck`, `build`; browser check of the whole loop locally.
- [ ] Commit; fetch origin; merge to `main`; push `main` and `main:deploy/render`; verify the live site.
