# Neon, Azure OpenAI and Groq

HireFlo v2.0 can keep its data in **Neon** (serverless PostgreSQL) and use two AI providers:
**Azure OpenAI** (your Azure for Students credit) and **Groq** (free tier). Everything is optional: with none of it
configured, HireFlo runs on local SQLite with the built-in rule extractor and the policy engine.

All settings go in `backend/.env` (copy `backend/.env.example` if you don't have one). Never commit that file.

---

## 1. Move the database to Neon

1. Sign up at **neon.com** (GitHub or Google login works) and create a project. Pick the region closest to you
   (Singapore, `ap-southeast-1`, for India).
2. On the project dashboard click **Connect**. Keep **Connection pooling** switched on and copy the connection string.
   It looks like:
   `postgresql://neondb_owner:XXXX@ep-cool-name-123456-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`
3. Copy your existing local data (students, drives, offers, events) to Neon. From `backend` with the venv active:

   ```powershell
   .venv\Scripts\python -m app.migrate_db --to "postgresql://neondb_owner:XXXX@ep-...-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
   ```

   It creates the tables on Neon, copies every row in one transaction and fixes the id counters. If Neon already
   has data it stops; add `--replace` to overwrite it. To start fresh instead, skip this step and run
   `python -m app.seed --reset` after step 4.
4. Put the same string in `backend/.env`:

   ```
   DATABASE_URL=postgresql://neondb_owner:XXXX@ep-...-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```

5. Restart the API (`start.ps1` or `uvicorn app.main:app --reload`). Check
   **http://localhost:8000/api/health**: it should say `"database": "neon", "database_ok": true`.

Notes
* Paste the URL exactly as Neon gives it. HireFlo switches it to the psycopg 3 driver itself.
* Neon's free compute sleeps after a few idle minutes; the first request afterwards takes a second or two.
  HireFlo checks connections before use, so a sleeping database never causes an error.
* Uploaded resumes are files in `backend/data/uploads`, not database rows. They stay on your machine.
* With Docker: put `DATABASE_URL=...` in a `.env` file next to `docker-compose.yml`; the local `db` container is
  then simply unused.

---

## 2. Azure OpenAI with Azure for Students

Azure for Students gives US$100 of credit for 12 months when you verify with your institute e-mail (normally no
credit card is needed). The credit pays for Azure OpenAI usage. A small model such as `gpt-4o-mini` or `gpt-4.1-mini` costs a few
cents for a whole demo.

1. **Activate the credit:** go to **azure.microsoft.com/free/students**, choose *Start free*, and sign in with your
   `@nitc.ac.in` account. You land in the Azure portal (**portal.azure.com**) with an *Azure for Students*
   subscription.
2. **Find the regions you're allowed to use.** Student subscriptions only allow a handful of regions. In the portal
   search for **Policy** → *Authoring → Assignments* → **Allowed resource deployment regions** → *Parameters*.
   Note the list (for example `southindia`, `centralindia`, `eastus`, `swedencentral`, `eastus2`).
3. **Create the resource.** Open **ai.azure.com** (Microsoft Foundry) and create a project, or in the portal search
   **Azure OpenAI** → *Create*. Choose your student subscription, a new resource group (`hireflo-rg`), a region
   **from your allowed list**, a name (`hireflo-ai`) and pricing tier *Standard S0*.
4. **Deploy a model.** In Foundry: *Models + endpoints* (or *Build → Models*) → **Deploy model** → pick
   `gpt-4o-mini` (or `gpt-4.1-mini`) → deployment type *Global Standard* → name the deployment, e.g. `gpt-4o-mini`.
5. **Copy endpoint and key.** In the portal open the resource → **Keys and Endpoint**. Copy *KEY 1* and the
   *Endpoint* (`https://hireflo-ai.openai.azure.com/`).
6. **Add to `backend/.env`:**

   ```
   AZURE_OPENAI_ENDPOINT=https://hireflo-ai.openai.azure.com/
   AZURE_OPENAI_API_KEY=<KEY 1>
   AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
   ```

   The deployment name is the one *you* typed in step 4. A Foundry project endpoint
   (`https://x.services.ai.azure.com/api/projects/...`) also works.

If it doesn't work
* **`RequestDisallowedByAzure` when creating the resource:** the region isn't in your allowed list (step 2).
* **"Insufficient quota" / 0 TPM when deploying:** some student subscriptions start with no Azure OpenAI quota.
  Try another allowed region, or request quota from the Foundry *Quota* page. If none of your regions offers it,
  use Groq alone. HireFlo works fully with just one provider.
* **401 from the Test button:** wrong key, or the key belongs to a different resource than the endpoint.
* **404 "DeploymentNotFound":** `AZURE_OPENAI_DEPLOYMENT` must be the deployment name, not the model name, if
  you named them differently.

---

## 3. Groq (free, very fast)

1. Go to **console.groq.com**, sign in, open **API Keys** → *Create API Key* and copy it (it is shown once).
2. Add to `backend/.env`:

   ```
   GROQ_API_KEY=gsk_...
   # optional, these are the defaults
   GROQ_MODEL=llama-3.3-70b-versatile
   GROQ_FAST_MODEL=llama-3.1-8b-instant
   ```

The free tier allows about 30 requests a minute for the whole account. HireFlo limits the assistant to 25 a minute
across all users (10 per user), so a demo never hits Groq's limit.

---

## 4. What uses which provider

| Task | First choice | Falls back to |
| --- | --- | --- |
| HireFlo Assistant chat (students and cell) | Groq (`GROQ_MODEL`) for speed | Azure, then the generic provider |
| JD → eligibility rules | Azure (best at structured JSON) | Groq, then pattern extractor |
| Plain-language policy answers | Azure | Groq (`GROQ_FAST_MODEL`) |

Change the order with `LLM_CHAT_PROVIDER` / `LLM_EXTRACT_PROVIDER` (`auto`, `azure`, `groq`, `generic`).
If a provider fails (bad key, quota, outage) the next one is tried automatically.

Restart the API, sign in as the placement cell and open **AI assistant** in the sidebar. **Connected providers**
lists what HireFlo found; **Test** sends a one-word request to each and shows the latency or the error.

---

## 5. What the assistant sends to the AI (privacy)

* **Students:** their own programme, branch, CGPA, backlogs and skills, the drives they can see with their own
  eligibility reasons, their own applications, interviews and offers, and the placement policy.
  **No name, roll number or e-mail, and never another student's data.**
* **Placement cell:** totals only (placement percentages, branch counts, drive counts, forecast, inbox counts).
  **No individual student.**
* For eligibility and policy questions, the deterministic policy engine's answer is passed in as an authoritative
  fact; the model is told never to contradict it.
* Without an AI provider, students still get the policy engine's answer and the cell gets a season summary.

Questions and answers are not stored on the server (the chat lives in the browser tab). The audit log records only
that the assistant was used and which provider answered.
