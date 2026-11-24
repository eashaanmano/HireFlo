# DriveOps architecture

## Data flow

```mermaid
flowchart TB
  R[Recruiter<br/>magic link] <--> API
  C[Placement cell<br/>OTP or Google session] <--> API
  S[Student PWA<br/>OTP or Google session] <--> API
  G[Google<br/>OpenID Connect] -. sign-in .-> API
  P[Panel<br/>sub-link] <--> API
  API[FastAPI routers<br/>auth · RBAC · CSRF · validation] --> SVC
  subgraph SVC[Services]
    E[Eligibility + policy engine]
    J[JD extractor<br/>patterns or LLM]
    X[CP-SAT scheduler]
    L[Live drive-day ops<br/>projections · re-plans]
    I[Insights]
  end
  SVC --> DB[(PostgreSQL / SQLite)]
  SVC --> EV[Event log]
  EV -->|after commit| WS[WebSocket fan-out]
  WS --> R & C & S & P
  SVC --> OUT[Exports: ATS CSV · .ics · resume zip · e-mail]
```

Every state change calls `services.events.emit()`. The event row is written in the same transaction as the change;
after the commit, a minimal notice (event type and ids, never personal data) is pushed to every connected screen,
and each screen refetches what it shows. The same rows form the audit log and feed analytics.

## Module boundaries

| Module | Depends on | Responsibility |
| --- | --- | --- |
| `services/scheduler.py` | OR-Tools only | Pure CP-SAT model in integer minutes. No database, fully unit-tested, plus an independent `validate()` checker for every hard constraint |
| `services/live.py` | scheduler, models | Converts slots to solver inputs: first schedules, projections, re-plan proposals, slot state machine |
| `services/policy.py` | pydantic | Versioned policy config and policy checks with stable rule codes (P0–P5) |
| `services/eligibility.py` | policy | Rules + policy → decision with a reason per check |
| `services/jd_parser.py` | httpx (optional LLM) | JD → proposed rules with source sentence and confidence |
| `services/insights.py` | eligibility, live | Student, cell and recruiter dashboards |
| `routers/*` | services, schemas | HTTP only: auth, validation, scoping, commits |

## Drive-day state machine (slot)

```
scheduled ──start──▶ in_progress ──complete(pass|fail)──▶ done
    │                                                   └─ pass + next interview round scheduled → new slot in that queue
    └─no-show──▶ no_show ──late check-in──▶ scheduled (and a re-plan is drafted)
```

## Re-planning

Triggered by a no-show, a late arrival, a panel running more than `DELAY_THRESHOLD_MIN` behind, or the cell. The solver
re-optimises every not-yet-started slot from *now*:

- panels become available when their current interview is projected to end;
- students not yet on site can't be pulled earlier than 15 minutes before their communicated time;
- the objective finishes the day earliest first, then pulls checked-in students forward, then minimises panel changes
  and time shifts so the plan stays recognisable.

The result is a proposal with before/after metrics. Nothing changes until the cell approves it; slots that started in
the meantime are skipped safely.
