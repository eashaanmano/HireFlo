"""Data out to existing tools: calendar invites, ATS-friendly CSVs, resume bundles, placement reports."""
from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from ..models import Application, Slot
from .resumes import resume_file

ATS_COLUMNS = ("candidate_id", "full_name", "email", "phone", "degree", "branch", "cgpa", "backlogs",
               "graduation_year", "skills", "stage", "status")


def _ics_dt(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def slots_to_ics(slots: Iterable[Slot], now: datetime) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//HireFlo//Placement//EN", "CALSCALE:GREGORIAN"]
    for s in slots:
        drive = s.application.drive
        summary = f"{drive.company.name}: {s.round.name}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:driveops-slot-{s.id}@driveops",
            f"DTSTAMP:{_ics_dt(now)}",
            f"DTSTART:{_ics_dt(s.start_at)}",
            f"DTEND:{_ics_dt(s.end_at)}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"LOCATION:{_ics_escape(f'{s.panel.room} ({s.panel.name})')}",
            f"DESCRIPTION:{_ics_escape(f'{drive.role}. Check in at the venue QR when you arrive.')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def stage_label(app: Application) -> str:
    if app.status.value in ("selected", "withdrawn", "applied"):
        return app.status.value
    if app.status.value == "rejected":
        rnd = next((r for r in app.drive.rounds if r.seq == app.rejected_round), None)
        return f"rejected at {rnd.name}" if rnd else "rejected at screening"
    nxt = next((r for r in app.drive.rounds if r.seq == app.rounds_cleared + 1), None)
    return f"awaiting {nxt.name}" if nxt else "in process"


def applications_csv(apps: Sequence[Application]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(ATS_COLUMNS)
    for a in apps:
        s = a.student
        w.writerow([s.roll_no, s.name, s.email, s.phone or "", s.programme, s.branch, f"{s.cgpa:.2f}", s.backlogs,
                    s.grad_year, "; ".join(s.skills), stage_label(a), a.status.value])
    return out.getvalue()


def resume_bundle(apps: Sequence[Application], upload_root: Path) -> bytes:
    """Zip of shortlisted candidates' resumes, ordered and named for the recruiter."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        index = ["order,roll_no,name,file"]
        for i, a in enumerate(apps, start=1):
            resume = next((r for r in a.student.resumes if r.id == a.resume_id), None) or \
                (a.student.resumes[0] if a.student.resumes else None)
            if resume is None:
                continue
            path = resume_file(resume, upload_root).resolve()
            if not path.is_file() or upload_root.resolve() not in path.parents:
                continue
            name = f"{i:03d}_{a.student.roll_no}_{a.student.name.replace(' ', '_')}.pdf"
            z.write(path, name)
            index.append(f"{i},{a.student.roll_no},{a.student.name},{name}")
        z.writestr("index.csv", "\n".join(index) + "\n")
    return buf.getvalue()


def rows_csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue()
