"""Placement cell API (people, companies, policy, insights): 'manage by exception'."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, or_, select

from ..core.deps import AdminUser, CellUser, DbSession
from ..core.timeutil import utcnow
from ..models import (
    Company,
    Dispute,
    DisputeStatus,
    Drive,
    DriveStatus,
    EligibilityDecision,
    Event,
    Policy,
    Role,
    Student,
    User,
)
from ..schemas import (
    CompanyIn,
    CompanyOut,
    CompanyUpdate,
    DisputeResolve,
    PolicyIn,
    PolicyOut,
    SimulationIn,
    StudentCreate,
    StudentOut,
    StudentUpdate,
    UserIn,
    UserOut,
    UserUpdate,
)
from ..services import analytics, company_profile, eligibility, forecast, importer, insights, retrospective
from ..services.events import emit
from ..services.exports import rows_csv
from ..services.notifications import notify
from ..services.policy import active_policy, placements_for_all, save_new_version
from ..services.simulator import SimConfig, run_simulation
from .common import bad_request, conflict, get_or_404

router = APIRouter(prefix="/api/cell", tags=["placement cell"])

TEMPLATE_CSV = ("Roll No,Name,Email,Phone,Branch,Programme,CGPA,Backlogs,Graduation Year,Skills\n"
                "B210001CS,Asha Nair,asha.b210001cs@nitc.ac.in,9876543210,CSE,BTECH,8.42,0,2026,"
                "python; sql; react\n")


def _recompute_upcoming(db: DbSession) -> None:
    for d in db.scalars(select(Drive).where(Drive.status.in_((DriveStatus.draft, DriveStatus.open)))):
        eligibility.recompute_drive(db, d)


# ---------------------------------------------------------------- insights
@router.get("/inbox")
def inbox(user: CellUser, db: DbSession) -> list[dict[str, Any]]:
    return insights.exceptions_inbox(db)


@router.get("/stats")
def stats(user: CellUser, db: DbSession) -> dict[str, Any]:
    return insights.placement_stats(db)


@router.get("/analytics")
def season(user: CellUser, db: DbSession) -> dict[str, Any]:
    return analytics.season_analytics(db)


@router.get("/calendar")
def calendar(user: CellUser, db: DbSession) -> list[dict[str, Any]]:
    return analytics.season_calendar(db)


@router.get("/coverage")
def coverage(user: CellUser, db: DbSession) -> dict[str, Any]:
    return insights.coverage_gaps(db)


@router.get("/at-risk")
def at_risk(user: CellUser, db: DbSession) -> list[dict[str, Any]]:
    return insights.at_risk_students(db)


# ---------------------------------------------------------------- enhancements: forecast, early warning, retrospective
@router.get("/forecast")
def placement_forecast(user: CellUser, db: DbSession) -> dict[str, Any]:
    """FUT-1: expected placements per branch by season end, with the basis of the estimate."""
    return forecast.branch_forecast(db)


@router.get("/early-warning")
def early_warning(user: CellUser, db: DbSession) -> list[dict[str, Any]]:
    """FUT-2: unplaced students likely to stay unplaced, with reasons and a suggested intervention (cell only)."""
    return forecast.early_warning(db)


@router.get("/reports/retrospective")
def retrospective_report(user: CellUser, db: DbSession, season: str | None = Query(default=None, max_length=9)
                         ) -> dict[str, Any]:
    """FUT-5: the season retrospective; `season` like 2026-27, latest season by default."""
    report = retrospective.season_report(db, season)
    report["available_seasons"] = retrospective.seasons(db)
    return report


@router.get("/reports/retrospective.csv")
def retrospective_csv(user: CellUser, db: DbSession, season: str | None = Query(default=None, max_length=9)
                      ) -> PlainTextResponse:
    """FUT-6: CSV export of the retrospective."""
    r = retrospective.season_report(db, season)
    return PlainTextResponse(retrospective.report_csv(r), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="retrospective_{r["season"]}.csv"'})


@router.get("/reports/retrospective.pdf")
def retrospective_pdf(user: CellUser, db: DbSession, season: str | None = Query(default=None, max_length=9)
                      ) -> Response:
    """FUT-6: PDF export of the retrospective."""
    r = retrospective.season_report(db, season)
    return Response(retrospective.report_pdf(r), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="retrospective_{r["season"]}.pdf"'})


@router.get("/reports/placements.csv")
def placement_report(user: CellUser, db: DbSession) -> PlainTextResponse:
    _, cfg = active_policy(db)
    placements = placements_for_all(db)
    rows = []
    for s in db.scalars(select(Student).where(Student.archived.is_(False)).order_by(Student.roll_no)):
        p = placements.get(s.id)
        rows.append([s.roll_no, s.name, s.branch, s.programme, f"{s.cgpa:.2f}", "yes" if p else "no",
                     "; ".join(p.companies) if p else "", f"{p.best_ctc:g}" if p and p.best_ctc else "",
                     cfg.tier_name(p.best_ctc) if p and p.best_ctc else ""])
    csv_text = rows_csv(["roll_no", "name", "branch", "programme", "cgpa", "placed", "companies", "best_ctc_lpa",
                         "tier"], rows)
    return PlainTextResponse(csv_text, media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="placements.csv"'})


@router.post("/simulate")
def simulate(body: SimulationIn, user: CellUser) -> dict[str, object]:
    cfg = SimConfig(**body.model_dump())
    return run_simulation(cfg).as_dict()


# ---------------------------------------------------------------- students
@router.get("/students")
def list_students(user: CellUser, db: DbSession, q: str | None = Query(default=None, max_length=80),
                  branch: str | None = None, include_archived: bool = False,
                  page: int = Query(default=1, ge=1), per_page: int = Query(default=50, ge=1, le=2000)
                  ) -> dict[str, Any]:
    stmt = select(Student)
    if not include_archived:
        stmt = stmt.where(Student.archived.is_(False))
    if branch:
        stmt = stmt.where(Student.branch == branch.upper())
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Student.name.ilike(like), Student.roll_no.ilike(like), Student.email.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Student.roll_no).offset((page - 1) * per_page).limit(per_page)).all()
    return {"total": total, "page": page, "per_page": per_page,
            "items": [StudentOut.model_validate(s).model_dump(mode="json") for s in rows]}


@router.post("/students", response_model=StudentOut, status_code=status.HTTP_201_CREATED)
def create_student(body: StudentCreate, user: CellUser, db: DbSession) -> Any:
    if db.scalar(select(Student).where(or_(Student.roll_no == body.roll_no.upper(),
                                           Student.email == body.email.lower()))):
        raise conflict("A student with this roll number or e-mail already exists.")
    data = body.model_dump()
    data["roll_no"], data["email"] = body.roll_no.upper(), body.email.lower()
    s = Student(**data, verified=True)
    db.add(s)
    db.flush()
    db.add(User(email=s.email, name=s.name, role=Role.student, student_id=s.id))
    emit(db, "student.created", user.email, entity="student", entity_id=s.id)
    _recompute_upcoming(db)
    db.commit()
    return s


@router.get("/students/template.csv")
def student_template(user: CellUser) -> PlainTextResponse:
    return PlainTextResponse(TEMPLATE_CSV, media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="master_sheet_template.csv"'})


@router.post("/students/import")
async def import_students(user: CellUser, db: DbSession, file: UploadFile = File(...),
                          dry_run: bool = False) -> dict[str, Any]:
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise bad_request("The sheet must be 5 MB or smaller.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise bad_request("Save the sheet as CSV (UTF-8) and upload again.") from exc
    report = importer.import_students(db, text, dry_run=dry_run)
    if not dry_run:
        emit(db, "students.imported", user.email, entity="import",
             payload={"created": report.created, "updated": report.updated, "errors": len(report.errors)})
        if report.created or report.updated:
            _recompute_upcoming(db)
        db.commit()
    else:
        db.rollback()
    return report.as_dict()


@router.get("/students/{student_id}")
def get_student(student_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    s = get_or_404(db, Student, student_id, "Student")
    decisions = db.scalars(select(EligibilityDecision).where(EligibilityDecision.student_id == s.id)).all()
    apps = [{"drive_id": a.drive_id, "company": a.drive.company.name, "role": a.drive.role,
             "status": a.status.value, "rounds_cleared": a.rounds_cleared,
             "offer": a.offer.status.value if a.offer else None} for a in s.applications]
    return {"student": StudentOut.model_validate(s).model_dump(mode="json"), "applications": apps,
            "eligible_drives": sum(d.eligible for d in decisions),
            "resumes": [{"id": r.id, "track": r.track, "filename": r.filename} for r in s.resumes]}


@router.patch("/students/{student_id}", response_model=StudentOut)
def update_student(student_id: int, body: StudentUpdate, user: CellUser, db: DbSession) -> Any:
    s = get_or_404(db, Student, student_id, "Student")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(s, k, v)
    if s.user and "name" in data:
        s.user.name = s.name
    if s.user and "archived" in data:
        s.user.active = not s.archived
    emit(db, "student.updated", user.email, entity="student", entity_id=s.id, payload={"fields": list(data)})
    if set(data) & {"cgpa", "backlogs", "branch", "programme", "grad_year", "archived"}:
        _recompute_upcoming(db)
    db.commit()
    return s


@router.delete("/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_student(student_id: int, user: CellUser, db: DbSession) -> None:
    s = get_or_404(db, Student, student_id, "Student")
    s.archived = True
    if s.user:
        s.user.active = False
    emit(db, "student.archived", user.email, entity="student", entity_id=s.id)
    _recompute_upcoming(db)
    db.commit()


# ---------------------------------------------------------------- companies
@router.get("/companies/{company_id}/profile")
def company_insight(company_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    """FUT-3: the company's profile built from its completed drives."""
    return company_profile.company_profile(db, get_or_404(db, Company, company_id, "Company"))


ACTIVE_DRIVE = (DriveStatus.draft, DriveStatus.open, DriveStatus.closed, DriveStatus.live)


def _company_out(c: Company) -> CompanyOut:
    out = CompanyOut.model_validate(c)
    out.active_drives = sum(d.status in ACTIVE_DRIVE for d in c.drives)
    out.live_drives = sum(d.status == DriveStatus.live for d in c.drives)
    return out


@router.get("/companies", response_model=list[CompanyOut])
def list_companies(user: CellUser, db: DbSession, include_archived: bool = False) -> Any:
    stmt = select(Company).order_by(Company.archived, Company.name)
    if not include_archived:
        stmt = stmt.where(Company.archived.is_(False))
    return [_company_out(c) for c in db.scalars(stmt)]


@router.post("/companies", response_model=CompanyOut, status_code=status.HTTP_201_CREATED)
def create_company(body: CompanyIn, user: CellUser, db: DbSession) -> Any:
    existing = db.scalar(select(Company).where(func.lower(Company.name) == body.name.lower()))
    if existing:
        if existing.archived:
            raise conflict("This company exists but is archived. Restore it from 'Show archived' instead.")
        raise conflict("A company with this name already exists.")
    c = Company(**body.model_dump())
    db.add(c)
    db.flush()
    emit(db, "company.created", user.email, entity="company", entity_id=c.id)
    db.commit()
    return _company_out(c)


@router.patch("/companies/{company_id}", response_model=CompanyOut)
def update_company(company_id: int, body: CompanyUpdate, user: CellUser, db: DbSession) -> Any:
    c = get_or_404(db, Company, company_id, "Company")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("archived") and not c.archived:
        _check_can_archive(c)
    for k, v in changes.items():
        setattr(c, k, v)
    emit(db, "company.updated", user.email, entity="company", entity_id=c.id)
    db.commit()
    return _company_out(c)


def _check_can_archive(c: Company) -> None:
    if any(d.status == DriveStatus.live for d in c.drives):
        raise conflict("This company has a drive running today. Complete it before archiving the company.")


@router.delete("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_company(company_id: int, user: CellUser, db: DbSession) -> None:
    """Soft delete: hides the company from lists and new drives. Existing drives, offers and history are kept."""
    c = get_or_404(db, Company, company_id, "Company")
    if c.archived:
        return
    _check_can_archive(c)
    c.archived = True
    emit(db, "company.archived", user.email, entity="company", entity_id=c.id)
    db.commit()


@router.post("/companies/{company_id}/restore", response_model=CompanyOut)
def restore_company(company_id: int, user: CellUser, db: DbSession) -> Any:
    c = get_or_404(db, Company, company_id, "Company")
    if c.archived:
        c.archived = False
        emit(db, "company.restored", user.email, entity="company", entity_id=c.id)
        db.commit()
    return _company_out(c)


# ---------------------------------------------------------------- policy
@router.get("/policy")
def get_policy(user: CellUser, db: DbSession) -> dict[str, Any]:
    row, cfg = active_policy(db)
    db.commit()
    versions = db.scalars(select(Policy).order_by(Policy.version.desc()).limit(20)).all()
    return {"active": PolicyOut.model_validate(row).model_dump(mode="json"), "rules": cfg.describe(),
            "versions": [PolicyOut.model_validate(v).model_dump(mode="json") for v in versions]}


@router.put("/policy", response_model=PolicyOut)
def put_policy(body: PolicyIn, user: AdminUser, db: DbSession) -> Any:
    row = save_new_version(db, body.config, body.note, user.email)
    emit(db, "policy.updated", user.email, entity="policy", entity_id=row.id, payload={"version": row.version})
    _recompute_upcoming(db)
    db.commit()
    return row


# ---------------------------------------------------------------- disputes
@router.get("/disputes")
def list_disputes(user: CellUser, db: DbSession, status_filter: str | None = Query(default=None, alias="status")
                  ) -> list[dict[str, Any]]:
    stmt = select(Dispute).order_by(Dispute.created_at.desc())
    if status_filter:
        stmt = stmt.where(Dispute.status == DisputeStatus(status_filter))
    out = []
    for d in db.scalars(stmt):
        dec = db.get(EligibilityDecision, (d.drive_id, d.student_id))
        out.append({"id": d.id, "student": d.student.name, "student_id": d.student_id, "roll_no": d.student.roll_no,
                    "drive_id": d.drive_id, "drive": f"{d.drive.company.name} ({d.drive.role})",
                    "message": d.message, "status": d.status.value, "resolution": d.resolution,
                    "reasons": dec.reasons if dec else [], "created_at": d.created_at.isoformat()})
    return out


@router.post("/disputes/{dispute_id}/resolve")
def resolve_dispute(dispute_id: int, body: DisputeResolve, user: CellUser, db: DbSession) -> dict[str, Any]:
    d = get_or_404(db, Dispute, dispute_id, "Dispute")
    if d.status != DisputeStatus.open:
        raise bad_request("This dispute is already resolved.")
    d.status = DisputeStatus(body.status)
    d.resolution = body.resolution
    d.resolved_by = user.email
    d.resolved_at = utcnow()
    eligibility.recompute_drive(db, d.drive)
    emit(db, "dispute.resolved", user.email, entity="dispute", entity_id=d.id, drive_id=d.drive_id,
         payload={"status": body.status})
    notify(db, [d.student_id], f"Your dispute for {d.drive.company.name} was "
           f"{'upheld' if body.status == 'upheld' else 'reviewed'}", body.resolution,
           link=f"/student/drives/{d.drive_id}")
    db.commit()
    return {"id": d.id, "status": d.status.value}


# ---------------------------------------------------------------- users & audit
@router.get("/users", response_model=list[UserOut])
def list_users(user: AdminUser, db: DbSession) -> Any:
    return db.scalars(select(User).where(User.role != Role.student).order_by(User.id)).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def invite_user(body: UserIn, user: AdminUser, db: DbSession) -> Any:
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise conflict("This e-mail already has an account.")
    u = User(email=body.email.lower(), name=body.name, role=Role(body.role))
    db.add(u)
    db.flush()
    emit(db, "user.invited", user.email, entity="user", entity_id=u.id, payload={"role": body.role})
    db.commit()
    return u


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, user: AdminUser, db: DbSession) -> Any:
    u = get_or_404(db, User, user_id, "User")
    if u.role == Role.student:
        raise bad_request("Student accounts are managed from the student directory.")
    if u.id == user.id and (body.active is False or (body.role and body.role != "admin")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't remove your own admin access.")
    if body.role:
        u.role = Role(body.role)
    if body.active is not None:
        u.active = body.active
    emit(db, "user.updated", user.email, entity="user", entity_id=u.id)
    db.commit()
    return u


@router.get("/audit")
def audit(user: CellUser, db: DbSession, drive_id: int | None = None, type_prefix: str | None = None,
          before_id: int | None = None, limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    stmt = select(Event).order_by(Event.id.desc()).limit(limit)
    if drive_id is not None:
        stmt = stmt.where(Event.drive_id == drive_id)
    if type_prefix:
        stmt = stmt.where(Event.type.startswith(type_prefix))
    if before_id:
        stmt = stmt.where(Event.id < before_id)
    return [{"id": e.id, "ts": e.ts.isoformat(), "type": e.type, "actor": e.actor, "entity": e.entity,
             "entity_id": e.entity_id, "drive_id": e.drive_id, "payload": e.payload} for e in db.scalars(stmt)]
