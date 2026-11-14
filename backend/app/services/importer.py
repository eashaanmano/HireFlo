"""Import the placement cell's existing master sheet (CSV). No student ever fills a signup form.

Headers are matched loosely ("Roll No", "roll_no", "Roll Number" all work). Every row is validated; bad rows
are reported with the exact problem and skipped, good rows are created or updated by roll number.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Role, Student, User

BRANCHES = {"CSE", "ECE", "EEE", "ME", "CE", "CHE", "PE"}
PROGRAMMES = {"BTECH", "MTECH", "MCA", "MSC"}
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "roll_no": ("roll no", "roll number", "roll", "rollno", "roll_no", "register number", "reg no"),
    "name": ("name", "student name", "full name"),
    "email": ("email", "e-mail", "institute email", "mail", "email id"),
    "phone": ("phone", "mobile", "contact", "phone number"),
    "branch": ("branch", "department", "dept"),
    "programme": ("programme", "program", "degree", "course"),
    "cgpa": ("cgpa", "cpi", "gpa"),
    "backlogs": ("backlogs", "active backlogs", "arrears"),
    "grad_year": ("graduation year", "grad year", "batch", "passing year", "grad_year", "year of passing"),
    "skills": ("skills", "key skills"),
}
REQUIRED = ("roll_no", "name", "email", "branch", "programme", "cgpa", "grad_year")


@dataclass
class RowError:
    row: int
    roll_no: str | None
    problems: list[str]


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[RowError] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"created": self.created, "updated": self.updated, "unchanged": self.unchanged,
                "errors": [e.__dict__ for e in self.errors], "columns": self.columns, "dry_run": self.dry_run}


def _norm(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lower().replace("_", " "))


def map_headers(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for h in headers:
        n = _norm(h)
        for key, aliases in HEADER_ALIASES.items():
            if key not in mapping and (n in aliases or n == key.replace("_", " ")):
                mapping[key] = h
    return mapping


def _normalise_branch(raw: str) -> str:
    v = raw.strip().upper().replace(".", "")
    aliases = {"CS": "CSE", "COMPUTER SCIENCE": "CSE", "EC": "ECE", "EE": "EEE", "MECH": "ME", "CIVIL": "CE",
               "CHEM": "CHE", "CHEMICAL": "CHE", "PROD": "PE"}
    return aliases.get(v, v)


def _normalise_programme(raw: str) -> str:
    v = raw.strip().upper().replace(".", "").replace(" ", "")
    return {"BE": "BTECH", "ME": "MTECH", "MS": "MSC"}.get(v, v)


def parse_row(raw: dict[str, str], mapping: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    get = lambda k: (raw.get(mapping[k]) or "").strip() if k in mapping else ""  # noqa: E731
    problems: list[str] = []
    data: dict[str, Any] = {}
    for k in REQUIRED:
        if not get(k):
            problems.append(f"missing {k.replace('_', ' ')}")
    data["roll_no"] = get("roll_no").upper()
    data["name"] = re.sub(r"\s+", " ", get("name")).title() if get("name") else ""
    email = get("email")
    if email:
        try:
            data["email"] = validate_email(email, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            problems.append(f"invalid email '{email}'")
    data["phone"] = re.sub(r"[^\d+]", "", get("phone")) or None
    branch = _normalise_branch(get("branch")) if get("branch") else ""
    if branch and branch not in BRANCHES:
        problems.append(f"unknown branch '{get('branch')}'")
    data["branch"] = branch
    programme = _normalise_programme(get("programme")) if get("programme") else ""
    if programme and programme not in PROGRAMMES:
        problems.append(f"unknown programme '{get('programme')}'")
    data["programme"] = programme
    try:
        if get("cgpa"):
            data["cgpa"] = round(float(get("cgpa")), 2)
            if not 0 <= data["cgpa"] <= 10:
                problems.append("CGPA must be between 0 and 10")
    except ValueError:
        problems.append(f"CGPA '{get('cgpa')}' is not a number")
    try:
        data["backlogs"] = int(get("backlogs") or 0)
        if data["backlogs"] < 0:
            problems.append("backlogs cannot be negative")
    except ValueError:
        problems.append(f"backlogs '{get('backlogs')}' is not a whole number")
    try:
        if get("grad_year"):
            data["grad_year"] = int(get("grad_year"))
            if not 2000 <= data["grad_year"] <= 2100:
                problems.append("graduation year looks wrong")
    except ValueError:
        problems.append(f"graduation year '{get('grad_year')}' is not a year")
    skills = get("skills")
    data["skills"] = [s.strip().lower() for s in re.split(r"[;,|]", skills) if s.strip()] if skills else []
    return data, problems


def import_students(db: Session, content: str, *, dry_run: bool = False) -> ImportReport:
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    mapping = map_headers(reader.fieldnames or [])
    report = ImportReport(columns=mapping, dry_run=dry_run)
    missing_cols = [k for k in REQUIRED if k not in mapping]
    if missing_cols:
        report.errors.append(RowError(1, None, [f"missing column for {', '.join(missing_cols)}"]))
        return report

    seen_rolls: dict[str, int] = {}
    seen_emails: dict[str, int] = {}
    existing = {s.roll_no.upper(): s for s in db.scalars(select(Student))}
    emails_taken = {s.email.lower(): s.roll_no.upper() for s in existing.values()}

    for line_no, raw in enumerate(reader, start=2):
        if not any((v or "").strip() for v in raw.values()):
            continue
        data, problems = parse_row(raw, mapping)
        roll = data.get("roll_no") or None
        if roll and roll in seen_rolls:
            problems.append(f"duplicate roll number (also on row {seen_rolls[roll]})")
        email = data.get("email")
        if email and email in seen_emails:
            problems.append(f"duplicate email (also on row {seen_emails[email]})")
        if email and email in emails_taken and emails_taken[email] != roll:
            problems.append("email already belongs to another student")
        if problems:
            report.errors.append(RowError(line_no, roll, problems))
            continue
        assert roll and email
        seen_rolls[roll] = line_no
        seen_emails[email] = line_no
        student = existing.get(roll)
        if student is None:
            report.created += 1
            if not dry_run:
                student = Student(**data, verified=True)
                db.add(student)
                db.flush()
                db.add(User(email=email, name=data["name"], role=Role.student, student_id=student.id))
            continue
        changed = {k: v for k, v in data.items() if getattr(student, k) != v}
        if not changed:
            report.unchanged += 1
            continue
        report.updated += 1
        if not dry_run:
            for k, v in changed.items():
                setattr(student, k, v)
            if student.user and "email" in changed:
                student.user.email = email
    if not dry_run:
        db.flush()
    return report
