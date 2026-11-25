"""The placement cell's side of Job Notification Forms: send a link, review, approve into a drive or send back."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select

from ..core.deps import CellUser, DbSession
from ..models import JobNotification
from ..schemas import JnfApproveIn, JnfInviteIn, JnfNoteIn
from ..services import jnf
from ..services.events import emit
from ..services.notifications import send_email
from .common import conflict, get_or_404

router = APIRouter(prefix="/api/cell/jnf", tags=["cell: job notification forms"])


def _form(db: DbSession, jnf_id: int) -> JobNotification:
    return get_or_404(db, JobNotification, jnf_id, "Job notification")


def _mail(n: JobNotification, subject: str, body: str) -> bool:
    return bool(n.contact_email) and send_email(n.contact_email or "", subject, body)


@router.post("", status_code=status.HTTP_201_CREATED)
def invite(body: JnfInviteIn, user: CellUser, db: DbSession) -> dict[str, Any]:
    n = JobNotification(company_name=body.company_name.strip(), contact_name=body.contact_name,
                        contact_email=str(body.contact_email) if body.contact_email else None,
                        invited_by=user.email, status="invited", data={})
    db.add(n)
    db.flush()
    emit(db, "jnf.invited", user.email, entity="jnf", entity_id=n.id)
    db.commit()
    url = jnf.link_url(n)
    emailed = _mail(n, f"{n.company_name}: job notification form for NIT Calicut placements",
                    f"Hello{' ' + n.contact_name if n.contact_name else ''},\n\n"
                    f"The placement cell at NIT Calicut invites {n.company_name} to recruit this season. Please fill "
                    f"in the job notification form here (the link works for 30 days):\n\n{url}\n\n"
                    "As you set your criteria, the form shows how many students meet them. The placement cell "
                    "reviews every form before a drive opens to students.")
    return {"id": n.id, "url": url, "emailed": emailed}


@router.get("")
def list_forms(user: CellUser, db: DbSession) -> list[dict[str, Any]]:
    return [jnf.row(n) for n in db.scalars(select(JobNotification).order_by(JobNotification.id.desc()))]


@router.get("/{jnf_id}")
def get_form(jnf_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    n = _form(db, jnf_id)
    return {"jnf": {**jnf.row(n), "data": n.data or None},
            "review": jnf.review(db, n) if n.data else None, "url": jnf.link_url(n)}


@router.post("/{jnf_id}/approve")
def approve(jnf_id: int, body: JnfApproveIn, user: CellUser, db: DbSession) -> dict[str, Any]:
    n = _form(db, jnf_id)
    try:
        d, opened = jnf.approve(db, n, user.email, body.open_now)
    except jnf.JnfError as exc:
        raise conflict(str(exc)) from exc
    db.commit()
    link = jnf.recruiter_url(d)
    emailed = _mail(n, f"{n.company_name}: your job notification was approved",
                    f"The placement cell approved your job notification for {d.role}"
                    + (" and the drive is now open to eligible students." if opened else ".")
                    + f"\n\nYour recruiter link (shortlists, results, resumes and live interview progress):\n\n{link}")
    return {"drive_id": d.id, "opened": opened, "recruiter_url": link, "emailed": emailed}


@router.post("/{jnf_id}/request-changes")
def request_changes(jnf_id: int, body: JnfNoteIn, user: CellUser, db: DbSession) -> dict[str, Any]:
    n = _form(db, jnf_id)
    try:
        jnf.request_changes(db, n, body.note, user.email)
    except jnf.JnfError as exc:
        raise conflict(str(exc)) from exc
    db.commit()
    emailed = _mail(n, f"{n.company_name}: changes requested on your job notification",
                    f"The placement cell asked for a change:\n\n{body.note}\n\nOpen your form: {jnf.link_url(n)}")
    return {**jnf.row(n), "emailed": emailed}


@router.post("/{jnf_id}/decline")
def decline(jnf_id: int, body: JnfNoteIn, user: CellUser, db: DbSession) -> dict[str, Any]:
    n = _form(db, jnf_id)
    try:
        jnf.decline(db, n, body.note, user.email)
    except jnf.JnfError as exc:
        raise conflict(str(exc)) from exc
    db.commit()
    emailed = _mail(n, f"{n.company_name}: job notification declined",
                    f"The placement cell declined the job notification this season:\n\n{body.note}")
    return {**jnf.row(n), "emailed": emailed}


@router.post("/{jnf_id}/rotate")
def rotate(jnf_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    n = _form(db, jnf_id)
    jnf.rotate_link(db, n, user.email)
    db.commit()
    return {"url": jnf.link_url(n)}
