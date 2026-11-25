# Company intake (JNF link) and the "you're up next" alert

Approved in conversation on 2026-11-25 ("Yes build this and the alert"). Ships to local (`main`), the hosted demo
(`deploy/render`) and GitHub. Constraint from the user: the demo data and everything that works today must keep
working unchanged.

## Why

- **JNF intake.** HireFlo runs a drive from the moment it exists, but the cell still creates every drive by hand
  from a company's Word or Google-form Job Notification Form (JNF) and an e-mail thread. That retyping is the most
  error-prone input to eligibility, tiers and scheduling. A JNF link lets the company fill the form in HireFlo and
  the cell approve it into a drive.
- **Alert.** On drive day a student only knows their place while the page is open; the cell still phones people
  to call them in, and students who wander off become no-shows that trigger re-plans. One alert per interview,
  shortly before it, closes that gap.

## Goals, non-goals, anti-goals

- Goals: the full JNF loop (invite, fill, review, approve / ask for changes / decline, drive created); the alert
  in-app and by e-mail, once per interview.
- Non-goals: AI in either feature; new delivery channels (Web Push, Telegram wiring); panel creation from the JNF
  (panels stay with the recruiter link and the cell, as today); changes to the seed.
- Anti-goals: any change to existing tables or demo data; a public form that leaks student data (the form shows a
  count only); repeated alerts.

## Approaches considered

1. **Picked: a new `job_notifications` table and a signed `jnf` link** (same pattern as recruiter and panel links).
   `create_all()` at startup adds the table on Neon and locally without a reseed, so the demo data is untouched.
2. Create a draft `Drive` as soon as the form is submitted: rejected, because a half-checked submission would show
   up in Drives and the eligibility pipeline before the cell has looked at it, and "ask for changes" would mean
   editing a drive from outside.
3. A free-form JNF upload (PDF/Word) parsed into a drive: rejected for now; it needs AI to be useful, and the user
   chose a non-AI feature.

For the alert: dedupe through the existing `events` table (a `slot.called` event per slot) instead of a new column,
so no existing table changes and every alert is in the audit log.

## Design

### Alert (`services/live.py: call_next`)

- A waiting (`scheduled`) slot is called when its projected start is within `CALL_AHEAD_MIN` (15) minutes, or when
  at most `CALL_AHEAD_COUNT` (2) interviews are ahead of it on its panel and it starts within 60 minutes (so a
  multi-day round never calls tomorrow's first student today). "Ahead" and the projection are computed exactly as
  the student's drive-day banner does (`drive_projection`), so the alert never disagrees with the screen.
- Once per slot: skipped when a `slot.called` event exists for it.
- Delivery: in-app notification (`notify`, live over WebSocket) plus e-mail through `send_email`, which honours
  `EMAIL_ONLY_TO` (on the hosted demo only the presenter's inbox gets real mail).
- Text: "AMD: you're up next" / "AMD: you're up soon"; body names the round, panel, people ahead, minutes and room,
  and reminds a student who hasn't checked in to check in first.
- Runs from the drive-day monitor (every 30 s), right after a panel marks an interview done, and after a re-plan is
  approved.

### JNF intake

- **Table `job_notifications`:** company name, contact name and e-mail, optional `company_id`, `status`
  (`invited`, `submitted`, `changes_requested`, `approved`, `declined`), `link_version`, the form as JSON, the
  cell's note, `drive_id` once approved, who invited and decided, timestamps.
- **Link:** a signed token of kind `jnf` (id + version), valid `JNF_LINK_DAYS` (30). "New link" bumps the version
  and revokes the old one. The form stays editable while `invited`, `submitted` or `changes_requested`; it locks
  once approved or declined.
- **Form fields:** role; CTC that counts (LPA) and its breakup (base, variable, joining bonus, other); location;
  bond (months) and note; eligible branches and programmes; minimum CGPA; maximum backlogs; selection rounds (name,
  test or interview, minutes; 1 to 6); number of interview panels; preferred drive date; apply-by date; expected
  hires; JD text; notes for the cell.
- **Live count on the form:** how many students meet the criteria, with the tier the policy assigns to the CTC.
  Aggregates only, same engine as the recruiter what-if.
- **Cell review:** tier, eligible count, and flags: bond; variable pay of 25% or more; a breakup that doesn't add
  up to the CTC; another drive on the same date; no or few eligible students for the hires wanted.
- **Approve:** finds the company by name (case-insensitive) or creates it; creates the drive with its rounds,
  approved rules from the structured criteria, skills and any extra rules the JD extractor finds for fields the
  form didn't cover (those stay unapproved, as today). If nothing is left to approve and "open now" is ticked, the
  drive opens and eligible students are notified exactly as when the cell opens a drive. HR is e-mailed the
  recruiter link.
- **Ask for changes / decline:** a note to HR (by e-mail where allowed) and on the form.
- **Inbox:** a submitted JNF appears as "New job notification from …" with a Review button.
- **UI:** "Send job notification form" and a "Job notifications" list on the Companies page; a review page at
  `/cell/jnf/:id`; the public form at `/j/:token`.
- **Audit:** `jnf.invited`, `jnf.submitted`, `jnf.changes_requested`, `jnf.approved`, `jnf.declined`,
  `jnf.link_rotated`, `slot.called`.

## Calls made by the builder (not discussed)

- Thresholds 15 minutes / 2 ahead / 60-minute cap, link life 30 days, variable-pay flag at 25%.
- Approval opens the drive only when no rule is left to approve; otherwise it stays a draft and the existing
  "Approve eligibility rules" inbox item takes over.
- An archived company with the same name blocks approval with a message (restore it first), as drive creation does.
- No seed changes: on the demo, the cell creates a JNF link from Companies to show the flow.
