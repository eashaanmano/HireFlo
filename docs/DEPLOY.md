# Hosting HireFlo for free (Render + Neon)

The hosted demo runs on two free services:

| Piece | Service | Free tier | Why |
| --- | --- | --- | --- |
| App (API, WebSocket, React UI) | **Render** web service, Docker, Singapore | 512 MB RAM, 0.1 CPU, 750 h/month, no card | Builds the repo's `Dockerfile` on every push, serves HTTPS and WebSockets |
| Database | **Neon** PostgreSQL, Singapore | 0.5 GB, compute sleeps when idle, no card | Render's free disk is wiped on every restart; Neon keeps the data |

One container serves everything on one origin (`Dockerfile` at the repo root), so session cookies, the CSRF header
and `/ws` behave exactly as they do behind nginx in docker-compose.

**What to expect from the free tier**
* The service sleeps after 15 minutes without visitors. The next visit wakes it in about a minute (Render shows a
  loading page). **Open the link a couple of minutes before you present.**
* Uploaded resumes live on the container's disk and vanish on a restart. The seeded resumes are redrawn
  automatically when that happens; a resume someone uploaded has to be uploaded again.
* The instance gets a tenth of a CPU. Everything works, but the **Simulator**'s many short CP-SAT solves are
  starved, so its re-planning numbers come out worse than on a laptop. Show the Simulator from the local copy.
* The demo season is built around the moment it is seeded. Refresh it before a demo (step 4) so the AMD drive is
  live *right now*.

---

## 1. Neon: create the database

1. Sign in at **neon.com** (GitHub login works) and create a project: Postgres 17, region **AWS Asia Pacific
   (Singapore)**.
2. **Connect** → keep *Connection pooling* on → copy the connection string
   (`postgresql://neondb_owner:...@ep-...-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`).

## 2. Render: create the service from the Blueprint

1. Sign in at **render.com** with GitHub and allow Render to see the `HireFlo` repository (it can stay private).
2. **New → Blueprint** → pick the repository and the branch that holds `render.yaml`.
3. Render reads `render.yaml` and asks for `DATABASE_URL`: paste the Neon string. Everything else is preset
   (`DEMO_MODE=true`, `COOKIE_SECURE=true`, a generated `SECRET_KEY`, the e-mail allowlist).
4. **Apply.** The first build takes 5 to 10 minutes. On first start the empty Neon database is seeded with the demo
   season. The site is at `https://hireflo-<suffix>.onrender.com` (ours: https://hireflo-8w3b.onrender.com;
   `hireflo.onrender.com` belongs to someone else).
   `/api/health` should say `"database": "neon"`.

`PUBLIC_URL` (used in recruiter and panel links and the venue QR) follows Render's own address automatically.

## 3. Optional: AI, tracing and real OTP e-mail

Service → **Environment → Add from .env**, paste the lines you want, **Save, rebuild and deploy**:

```
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_AGENT_ID=...
```

**E-mail.** Free Render services can't reach SMTP ports 25, 465 or 587, so Gmail SMTP doesn't work from Render.
Use a relay that also listens on another port:

* **Resend** (simplest for one inbox). Sign up at resend.com *with the address that should receive codes*, create
  an API key, then:
  ```
  SMTP_HOST=smtp.resend.com
  SMTP_PORT=2587
  SMTP_USER=resend
  SMTP_PASSWORD=re_...
  SMTP_FROM=HireFlo <onboarding@resend.dev>
  ```
  Without a verified domain Resend only delivers to the account's own address, which is all the demo needs.
* **Brevo** (any recipient, 300 a day): verify a sender, create an SMTP key, use `smtp-relay.brevo.com`, port
  `2525`, your Brevo login as `SMTP_USER` and the key as `SMTP_PASSWORD`.

`EMAIL_ONLY_TO` (preset in `render.yaml`) limits real e-mail to the listed inboxes. The demo's other addresses are
fictional but look like real `@nitc.ac.in` ones, so nobody else is ever e-mailed. For every address that isn't
e-mailed, the OTP is printed in the service's **Logs** (`[driveops] OTP for ...`), just like the terminal locally.
In demo mode it is also shown on screen.

## 4. Refresh the demo data before presenting

The **Refresh demo data** GitHub Action re-seeds Neon from GitHub's runner (about a minute; the free Render
instance is too slow to seed quickly). It runs every morning at 08:00 IST and on demand.

One-time setup (repository → Settings → Secrets and variables → Actions):
* secret `DEMO_DATABASE_URL` = the Neon string
* variable `DEMO_URL` = the Render address (the workflow wakes the site afterwards)

Then, before a demo: **Actions → Refresh demo data → Run workflow**, or

```powershell
gh workflow run refresh-demo.yml
```

A refresh replaces all data, including anything changed during an earlier demo.

## 5. Changing the hosted app

Every push to the deployed branch rebuilds and redeploys automatically (5 to 10 minutes on the free plan). For a
change requested on the spot during an evaluation, show it on the local copy first; the hosted site follows after the
push. To deploy a different branch: service → **Settings → Build & Deploy → Branch**.
