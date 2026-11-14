"""Generate a realistic, fully fictional demo batch and a ready-to-run drive day.

    python -m app.seed --reset

Creates ~150 students (written first as a master-sheet CSV and imported through the real importer), PDF resumes,
six fictional companies with JDs parsed by the real extractor, completed drives with offers, open drives,
a draft drive with low-confidence rules, and a drive that is live *right now* with an overrunning panel,
a checked-in queue and a pending re-plan. Times are relative to when you run it, so re-seed before a demo.

All names, companies and e-mail addresses are invented.
"""
from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.config import get_settings
from .core.database import Base, SessionLocal, create_all, engine
from .core.security import create_link_token
from .core.timeutil import add_minutes, utcnow
from .models import (
    Application,
    AppStatus,
    Company,
    Dispute,
    Drive,
    DriveStatus,
    EligibilityDecision,
    EligibilityRule,
    Experience,
    Offer,
    OfferStatus,
    Panel,
    Resume,
    Role,
    Round,
    RoundKind,
    Slot,
    SlotStatus,
    Student,
    User,
)
from .services import eligibility, importer, jd_parser, live
from .services.policy import PolicyConfig, active_policy
from .services.workhours import WorkingHours

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
# The live demo drive is built around "now", whatever the hour you seed at, so it uses a round-the-clock day.
# Real drives use the working hours from settings or the ones chosen in the Schedule tab.
DEMO_HOURS = WorkingHours(day_start="00:00", day_end="24:00", breaks=(), max_days=2, skip_sundays=False)

FIRST = ["Aarav", "Diya", "Rahul", "Ananya", "Arjun", "Meera", "Vishnu", "Nila", "Karthik", "Sneha", "Adithya",
         "Gayathri", "Rohan", "Lakshmi", "Nikhil", "Aisha", "Sidharth", "Fathima", "Joel", "Keerthana", "Varun",
         "Anjali", "Hari", "Shreya", "Abhinav", "Neha", "Gokul", "Riya", "Imran", "Pooja", "Akash", "Devika",
         "Manu", "Sana", "Pranav", "Ishita", "Tejas", "Amala", "Kevin", "Nandana", "Yash", "Parvathy"]
LAST = ["Menon", "Krishnan", "Nambiar", "Pillai", "Nair", "Varma", "Iyer", "Thomas", "Joseph", "Kurian",
        "Reddy", "Rao", "Sharma", "Gupta", "Das", "Haque", "Patil", "Bhat", "Shetty", "Kumar", "Mathew", "George",
        "Ahmed", "Chandran", "Raj", "Suresh", "Mohan", "Prakash", "Unni", "Kaimal"]
BRANCH_MIX = [("CSE", 40), ("ECE", 30), ("EEE", 25), ("ME", 25), ("CE", 15), ("CHE", 15)]
ROLL_SUFFIX = {"CSE": "CS", "ECE": "EC", "EEE": "EE", "ME": "ME", "CE": "CE", "CHE": "CH"}
SKILL_POOLS = {
    "CSE": ["python", "java", "c++", "sql", "react", "node.js", "docker", "data structures", "algorithms",
            "operating systems", "dbms", "machine learning", "system design", "git", "aws", "linux"],
    "ECE": ["verilog", "vhdl", "fpga", "embedded", "c", "c++", "python", "matlab", "digital design", "rtos",
            "computer architecture", "signal processing", "linux"],
    "EEE": ["matlab", "simulink", "embedded", "c", "python", "power systems", "plc", "digital design",
            "verilog", "excel"],
    "ME": ["solidworks", "ansys", "matlab", "python", "excel", "cad", "manufacturing", "six sigma"],
    "CE": ["autocad", "staad pro", "excel", "revit", "project management", "python"],
    "CHE": ["aspen plus", "matlab", "excel", "python", "process design", "statistics"],
}

@dataclass
class Featured:
    roll: str
    name: str
    email: str
    branch: str
    programme: str
    cgpa: float
    backlogs: int
    skills: list[str]
    note: str


# The team's own demo accounts (fictional academic records).
FEATURED = [
    Featured("M250012CS", "Eashaan Manohar", "eashaan_m250012cs@nitc.ac.in", "CSE", "MTECH", 9.12, 0,
             ["c++", "python", "computer architecture", "verilog", "linux", "system design", "data structures"],
             "Checked in for today's AMD interview; live queue position"),
    Featured("M250047CS", "Midhun Unnikrishna Pillai", "midhun_m250047cs@nitc.ac.in", "CSE", "MTECH", 8.41, 0,
             ["python", "machine learning", "nlp", "sql", "pandas", "statistics"],
             "Pending Accenture offer (below 10 LPA); sees what accepting changes"),
    Featured("M250088CS", "Harsh Kumar Kashyap", "harsh_m250088cs@nitc.ac.in", "CSE", "MTECH", 8.15, 0,
             ["react", "typescript", "javascript", "node.js", "sql", "docker"],
             "Placed at Cognizant; used 1 of 2 chances at 10+ LPA drives"),
]


@dataclass(frozen=True)
class CompanySpec:
    name: str
    sector: str
    role: str
    ctc: float
    location: str
    branches: tuple[str, ...] | None    # None = all branches
    cgpa: float
    backlogs: int
    skills: str
    status: str                          # live | open | closed | draft | completed
    days: int = 0                        # completed: days ago; open: days until the deadline
    about: str = ""
    contact: str = "Campus Relations"


CIRCUIT = ("CSE", "ECE", "EEE")
# CTCs are illustrative demo values, not actual offers.
CATALOG: list[CompanySpec] = [
    # ---- live today
    CompanySpec("AMD", "Semiconductors", "Silicon Design Engineer", 21, "Bengaluru / Hyderabad", CIRCUIT, 7.5, 0,
                "Verilog, SystemVerilog, computer architecture, C++, Python, Linux", "live",
                about="RTL design and verification for CPU and GPU IP."),
    # ---- open for applications
    CompanySpec("Qualcomm", "Semiconductors", "Engineer, Embedded Software", 24, "Hyderabad", CIRCUIT, 7.5, 0,
                "C, C++, embedded, RTOS, operating systems, computer architecture", "open", 2,
                about="Modem and multimedia firmware for Snapdragon platforms."),
    CompanySpec("NVIDIA", "Semiconductors", "System Software Engineer", 32, "Pune / Bengaluru", CIRCUIT, 8.0, 0,
                "C++, CUDA, Linux, operating systems, computer architecture, Python", "open", 4,
                about="GPU driver, compiler and systems software."),
    CompanySpec("Intel", "Semiconductors", "SoC Validation Engineer", 18, "Bengaluru", CIRCUIT, 7.0, 0,
                "SystemVerilog, Python, digital design, computer architecture, Linux", "open", 3,
                about="Pre-silicon and post-silicon validation of client SoCs."),
    CompanySpec("Microsoft", "Software", "Software Engineer", 45, "Hyderabad / Bengaluru", ("CSE", "ECE"), 8.0, 0,
                "data structures, algorithms, system design, C++, Java, distributed systems", "open", 5,
                about="Azure and Microsoft 365 engineering teams."),
    CompanySpec("Goldman Sachs", "Investment banking", "Analyst, Engineering", 26, "Bengaluru", CIRCUIT, 7.5, 0,
                "Java, Python, data structures, algorithms, SQL, system design", "open", 6,
                about="Engineering division: trading platforms and risk systems."),
    CompanySpec("BNY", "Banking & asset servicing", "Software Engineer", 14, "Pune / Chennai", None, 7.0, 0,
                "Java, Spring, SQL, microservices, REST, Git", "open", 4,
                about="Platform engineering for custody and payments."),
    CompanySpec("Chevron", "Energy", "Data Engineer", 14, "Bengaluru", None, 7.0, 0,
                "Python, SQL, Spark, Azure, statistics, Power BI", "open", 7,
                about="Engineering and technology centre: data platforms for operations."),
    CompanySpec("Swiss Re", "Reinsurance", "Software Engineer", 13, "Bengaluru", ("CSE", "ECE", "EEE", "ME"), 7.0, 1,
                "Python, Java, SQL, Azure, REST", "open", 8,
                about="Risk modelling platforms and internal products."),
    CompanySpec("GE", "Industrial", "Edison Engineering Development Program", 12, "Bengaluru",
                ("ME", "EEE", "ECE", "CSE"), 7.0, 0, "MATLAB, Python, CAD, embedded, manufacturing", "open", 6,
                about="Rotational engineering program across aerospace and power."),
    CompanySpec("BEL", "PSU", "Probationary Engineer", 10.5, "Bengaluru", ("ECE", "EEE", "ME"), 6.5, 0,
                "embedded, digital design, signal processing, MATLAB", "open", 5,
                about="Defence electronics. PSU offer: accepting it ends the season (P3)."),
    CompanySpec("IOC", "PSU", "Graduate Engineer", 16, "Multiple refineries", ("CHE", "ME", "EEE"), 6.5, 0,
                "process design, Aspen Plus, MATLAB, power systems", "open", 9,
                about="Refinery operations. PSU offer: accepting it ends the season (P3)."),
    CompanySpec("Infosys", "IT services", "Specialist Programmer", 9.5, "Mysuru / Bengaluru", None, 6.5, 1,
                "Java, Python, data structures, SQL, Git", "open", 3,
                about="Product engineering track."),
    CompanySpec("Capgemini", "IT services", "Analyst", 5.75, "Pan India", None, 6.0, 1,
                "Java, SQL, Excel, Git", "open", 2, about="Technology consulting."),
    # ---- applications closed, waiting on the recruiter
    CompanySpec("KLA Tencor", "Semiconductor equipment", "Algorithm Engineer", 20, "Chennai", CIRCUIT, 7.5, 0,
                "C++, Python, computer vision, signal processing, algorithms", "closed", 4,
                about="Image-processing algorithms for wafer inspection."),
    CompanySpec("UST Global", "IT services", "Developer", 6, "Thiruvananthapuram / Kochi", None, 6.0, 1,
                "Java, JavaScript, SQL, React", "closed", 3, about="Digital transformation projects."),
    # ---- draft: JD parsed, rules awaiting approval
    CompanySpec("ARM", "Semiconductors", "Software Engineer, Compilers", 22, "Bengaluru", CIRCUIT, 7.5, 0,
                "C++, LLVM, computer architecture, Python, Linux", "draft", 10,
                about="Compiler and performance libraries for Arm architectures."),
    CompanySpec("Wells Fargo", "Banking", "Technology Analyst", 16, "Bengaluru / Hyderabad", None, 7.0, 0,
                "Java, Python, SQL, REST, Git", "draft", 12, about="Enterprise technology."),
    # ---- completed earlier this season (days ago)
    CompanySpec("TCS", "IT services", "Digital", 7.5, "Pan India", None, 6.0, 1,
                "Java, Python, SQL, data structures", "completed", 24, about="TCS Digital track."),
    CompanySpec("Cognizant", "IT services", "GenC Next", 6.75, "Chennai / Kochi", None, 6.0, 1,
                "Java, JavaScript, React, SQL", "completed", 20, about="Full-stack engineering track."),
    CompanySpec("Dell", "Technology", "Software Engineer", 12, "Bengaluru", CIRCUIT, 7.0, 0,
                "C++, Python, Linux, operating systems, computer networks", "completed", 17,
                about="Storage and infrastructure software."),
    CompanySpec("Deutsche Bank", "Investment banking", "Technology Analyst", 19, "Pune", None, 7.5, 0,
                "Java, Python, SQL, data structures, algorithms", "completed", 15, about="Corporate bank technology."),
    CompanySpec("JP Morgan", "Investment banking", "Software Engineer Program", 19, "Bengaluru / Hyderabad", None,
                7.5, 0, "Java, Python, SQL, React, AWS", "completed", 13, about="Corporate and investment bank."),
    CompanySpec("Warner Bros. Discovery", "Media & entertainment", "Software Engineer, Data", 16, "Hyderabad",
                ("CSE", "ECE"), 7.0, 0, "Python, SQL, Spark, AWS, Kafka", "completed", 11,
                about="Streaming data platforms."),
    CompanySpec("Amazon", "Software", "SDE I", 32, "Bengaluru / Hyderabad", ("CSE", "ECE", "EEE"), 7.5, 0,
                "data structures, algorithms, Java, C++, system design, AWS", "completed", 9,
                about="Retail and AWS engineering teams."),
    CompanySpec("BHEL", "PSU", "Engineer Trainee", 11, "Multiple units", ("EEE", "ME", "ECE"), 6.5, 0,
                "power systems, MATLAB, CAD, manufacturing", "completed", 7, about="Power plant equipment."),
    CompanySpec("HPCL", "PSU", "Mechanical / Chemical Engineer", 15, "Mumbai / Visakhapatnam", ("ME", "CHE"), 6.5,
                0, "process design, Aspen Plus, CAD, MATLAB", "completed", 6, about="Refining and marketing."),
    CompanySpec("Accenture", "IT services", "Associate Software Engineer", 6.5, "Pan India", None, 6.0, 1,
                "Java, Python, SQL, Excel", "completed", 3, about="Technology delivery."),
]

BRANCH_TEXT = {"CSE": "CSE", "ECE": "ECE", "EEE": "EEE", "ME": "Mechanical", "CE": "Civil", "CHE": "Chemical",
               "PE": "Production"}


def jd_for(c: CompanySpec) -> str:
    """A realistic JD in the shape recruiters send; the real extractor parses it into rules."""
    if c.name == "ARM":  # deliberately fuzzy wording: extracted with low confidence, lands in the inbox
        return (f"{c.name} - {c.role}, {c.location}.\n{c.about}\nCircuit branches only, B.Tech or M.Tech, "
                f"2026 graduates.\nCandidates should have 75% aggregate marks in graduation.\n"
                f"Skills: {c.skills}.\nPackage: {c.ctc:g} LPA (illustrative).")
    lines = [f"{c.name} is hiring a {c.role} in {c.location}.", c.about]
    if c.branches:
        names = [BRANCH_TEXT[b] for b in c.branches]
        joined = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]
        lines.append(f"Open to B.Tech and M.Tech students from {joined} branches graduating in 2026 batch.")
    else:
        lines.append("All branches are eligible.")
        lines.append("Open to B.Tech and M.Tech students graduating in 2026 batch.")
    if c.backlogs == 0:
        lines.append(f"Minimum CGPA of {c.cgpa:g} with no active backlogs.")
    else:
        lines.append(f"Minimum CGPA of {c.cgpa:g}.")
        lines.append(f"Maximum of {c.backlogs} backlog allowed.")
    lines.append(f"Skills: {c.skills}.")
    lines.append(f"CTC: {c.ctc:g} LPA (illustrative).")
    return "\n".join(lines)

# ---------------------------------------------------------------- batch generation
def _students(rng: random.Random, count: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for f in FEATURED:
        rows.append({"Roll No": f.roll, "Name": f.name, "Email": f.email, "Phone": f"98{rng.randint(10**7, 10**8 - 1)}",
                     "Branch": f.branch, "Programme": f.programme, "CGPA": f"{f.cgpa:.2f}",
                     "Backlogs": f.backlogs, "Graduation Year": 2026, "Skills": "; ".join(f.skills)})
    weights = [w for _, w in BRANCH_MIX]
    used = {f.roll for f in FEATURED}
    serial = 300
    while len(rows) < count:
        branch = rng.choices([b for b, _ in BRANCH_MIX], weights)[0]
        mtech = rng.random() < 0.17
        programme = "MTECH" if mtech else "BTECH"
        serial += rng.randint(1, 7)
        roll = f"{'M24' if mtech else 'B22'}{serial:04d}{ROLL_SUFFIX[branch]}"
        if roll in used:
            continue
        used.add(roll)
        first, last = rng.choice(FIRST), rng.choice(LAST)
        cgpa = round(min(9.85, max(5.6, rng.gauss(7.7, 0.85))), 2)
        backlog_p = 0.35 if cgpa < 6.8 else 0.08
        backlogs = 0 if rng.random() > backlog_p else rng.choice([1, 1, 2, 3])
        pool = SKILL_POOLS[branch]
        skills = rng.sample(pool, k=rng.randint(3, min(7, len(pool))))
        rows.append({"Roll No": roll, "Name": f"{first} {last}", "Email": f"{first.lower()}_{roll.lower()}@nitc.ac.in",
                     "Phone": f"9{rng.randint(10**8, 10**9 - 1)}", "Branch": branch, "Programme": programme,
                     "CGPA": f"{cgpa:.2f}", "Backlogs": backlogs, "Graduation Year": 2026,
                     "Skills": "; ".join(skills)})
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _new_batch_csv(rng: random.Random) -> list[dict[str, object]]:
    """A second sheet for the live import demo: 24 MCA/M.Sc students with four deliberately messy rows."""
    rows = []
    for i in range(24):
        prog = "MCA" if i % 3 else "MSC"
        roll = f"{'MC' if prog == 'MCA' else 'MS'}25{100 + i:04d}"
        first, last = rng.choice(FIRST), rng.choice(LAST)
        rows.append({"Roll Number": roll, "Student Name": f"{first} {last}",
                     "Institute Email": f"{first.lower()}_{roll.lower()}@nitc.ac.in",
                     "Mobile": f"9{rng.randint(10**8, 10**9 - 1)}", "Department": "CSE", "Degree": prog,
                     "CPI": f"{round(rng.uniform(6.4, 9.4), 2):.2f}", "Active Backlogs": 0, "Batch": 2026,
                     "Key Skills": "; ".join(rng.sample(SKILL_POOLS["CSE"], 4))})
    rows[5]["Institute Email"] = ""                       # missing e-mail
    rows[9]["Roll Number"] = rows[8]["Roll Number"]       # duplicate roll number
    rows[14]["CPI"] = "8,4"                               # decimal comma
    rows[19]["Department"] = "Mech Engg"                  # unknown branch label
    return rows


def _resume_pdf(path: Path, s: Student, track: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 18)
    c.drawString(64, y, s.name)
    y -= 20
    c.setFont("Helvetica", 10)
    c.drawString(64, y, f"{s.email}  |  {s.phone or ''}  |  {s.roll_no}")
    y -= 28
    for title, lines in (
        ("Education", [f"{s.programme.replace('TECH', '.Tech')} in {s.branch}, NIT Calicut (expected {s.grad_year})",
                       f"CGPA: {s.cgpa:.2f} / 10"]),
        ("Skills", [", ".join(s.skills)]),
        ("Projects", [f"{track} project built with {', '.join(s.skills[:3])}.",
                      "Course project: designed, tested and documented end to end."]),
    ):
        c.setFont("Helvetica-Bold", 12)
        c.drawString(64, y, title)
        y -= 16
        c.setFont("Helvetica", 10)
        for line in lines:
            c.drawString(72, y, line[:110])
            y -= 14
        y -= 10
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(64, 48, "Fictional resume generated for the HireFlo demo.")
    c.showPage()
    c.save()


# ---------------------------------------------------------------- drives
ADMIN = "placement.admin@nitc.ac.in"
STANDARD_ROUNDS: list[tuple[str, RoundKind, int, list[tuple[str, str, str | None]]]] = [
    ("Online test", RoundKind.test, 75, []),
    ("Technical interview", RoundKind.interview, 30,
     [("Panel A", "Seminar Hall 1", None), ("Panel B", "Seminar Hall 2", None)]),
    ("HR interview", RoundKind.interview, 20, [("HR", "Board Room", None)]),
]


def _drive(db: Session, company: Company, spec: CompanySpec, now: datetime) -> Drive:
    jd = jd_for(spec)
    ext = jd_parser.extract_rules(jd)
    if spec.status == "completed":
        deadline = now - timedelta(days=spec.days + 3)
    elif spec.status in ("closed", "live"):
        deadline = now - timedelta(days=max(spec.days, 2))
    else:
        deadline = now + timedelta(days=spec.days, hours=6)
    status = DriveStatus.open if spec.status == "live" else DriveStatus(spec.status)
    approve = spec.status != "draft"
    d = Drive(company_id=company.id, role=spec.role, jd_text=jd, ctc_lpa=spec.ctc, status=status,
              apply_deadline=deadline, target_hires=3 if spec.ctc >= 15 else 6, skills=ext.skills,
              location=spec.location)
    for r in ext.rules:
        d.rules.append(EligibilityRule(field=r.field, op=r.op, value=r.value, source_text=r.source_text,
                                       confidence=r.confidence, approved=approve,
                                       approved_by=ADMIN if approve else None))
    db.add(d)
    db.flush()
    eligibility.recompute_drive(db, d)
    return d


def _rounds(db: Session, d: Drive, spec: list[tuple[str, RoundKind, int, list[tuple[str, str, str | None]]]]
            ) -> list[Round]:
    out = []
    for seq, (name, kind, dur, panels) in enumerate(spec, start=1):
        r = Round(drive_id=d.id, name=name, kind=kind, seq=seq, duration_min=dur)
        db.add(r)
        db.flush()
        for pname, room, who in panels:
            db.add(Panel(drive_id=d.id, round_id=r.id, name=pname, room=room, interviewer=who))
        out.append(r)
    db.flush()
    db.refresh(d)
    return out


def _eligible_students(db: Session, d: Drive) -> list[Student]:
    ids = [sid for sid, in db.execute(select(EligibilityDecision.student_id).where(
        EligibilityDecision.drive_id == d.id, EligibilityDecision.eligible.is_(True)))]
    return list(db.scalars(select(Student).where(Student.id.in_(ids)).order_by(Student.id)))


def _apply(db: Session, d: Drive, students: list[Student], at: datetime) -> list[Application]:
    apps = []
    for s in students:
        a = Application(drive_id=d.id, student_id=s.id, resume_id=s.resumes[0].id if s.resumes else None,
                        created_at=at, updated_at=at)
        db.add(a)
        apps.append(a)
    db.flush()
    return apps


def _past_interviews(db: Session, d: Drive, rnd: Round, apps: list[Application], day: datetime,
                     pass_ids: set[int], rng: random.Random) -> None:
    panels = rnd.panels
    for i, a in enumerate(apps):
        p = panels[i % len(panels)]
        start = add_minutes(day, (i // len(panels)) * rnd.duration_min)
        actual = add_minutes(start, rng.randint(0, 6))
        db.add(Slot(drive_id=d.id, round_id=rnd.id, panel_id=p.id, application_id=a.id, start_at=start,
                    end_at=add_minutes(start, rnd.duration_min), status=SlotStatus.done, checked_in_at=start,
                    actual_start=actual, actual_end=add_minutes(actual, rnd.duration_min + rng.randint(-4, 9)),
                    result="pass" if a.id in pass_ids else "fail"))
        if a.id in pass_ids:
            a.rounds_cleared = rnd.seq
        else:
            a.status, a.rejected_round = AppStatus.rejected, rnd.seq


@dataclass
class Season:
    """Tracks who is placed while completed drives are replayed in date order, so history obeys the policy."""

    cfg: PolicyConfig
    placed: dict[int, tuple[float, str]]
    attempts: dict[int, int]

    def may_apply(self, sid: int, ctc: float) -> bool:
        held = self.placed.get(sid)
        if held is None:
            return True
        if self.cfg.is_freezing(*held):
            return False
        return ctc >= self.cfg.freeze_at_ctc_lpa and self.attempts.get(sid, 0) < self.cfg.upgrade_attempts


def _completed_drive(db: Session, d: Drive, spec: CompanySpec, rng: random.Random, now: datetime, season: Season,
                     *, exclude: set[int], accept_for: set[int] = frozenset(),  # type: ignore[assignment]
                     pending_for: set[int] = frozenset(), n_apply: int = 34, accepted: int = 2) -> None:  # type: ignore[assignment]
    rounds = _rounds(db, d, STANDARD_ROUNDS)
    day = (now - timedelta(days=spec.days)).replace(hour=4, minute=0, second=0)
    keep = set(accept_for) | set(pending_for)
    pool = [s for s in _eligible_students(db, d) if s.id not in exclude or s.id in keep]
    pool = [s for s in pool if s.id in keep or season.may_apply(s.id, spec.ctc)]
    must = [s for s in pool if s.id in keep]
    others = [s for s in pool if s.id not in keep]
    rng.shuffle(others)
    apps = _apply(db, d, must + others[: max(0, n_apply - len(must))], day - timedelta(days=3))
    for a in apps:
        if a.student_id in season.placed:
            season.attempts[a.student_id] = season.attempts.get(a.student_id, 0) + 1
    shortlisted = [a for a in apps if a.student_id in keep or rng.random() < 0.7]
    for a in apps:
        a.status = AppStatus.in_process if a in shortlisted else AppStatus.rejected
    oa_pass = [a for a in shortlisted if a.student_id in keep or rng.random() < 0.5]
    for a in shortlisted:
        if a in oa_pass:
            a.rounds_cleared = 1
        else:
            a.status, a.rejected_round = AppStatus.rejected, 1
    tech_pass = {a.id for a in oa_pass if a.student_id in keep or rng.random() < 0.45}
    _past_interviews(db, d, rounds[1], oa_pass, day, tech_pass, rng)
    hr_apps = [a for a in oa_pass if a.id in tech_pass]
    hr_pass = {a.id for a in hr_apps if a.student_id in keep or rng.random() < 0.75}
    _past_interviews(db, d, rounds[2], hr_apps, add_minutes(day, 300), hr_pass, rng)
    selected = sorted((a for a in hr_apps if a.id in hr_pass), key=lambda a: a.student_id not in keep)
    taken = 0
    for a in selected:
        a.status = AppStatus.selected
        if a.student_id in pending_for:
            db.add(Offer(application_id=a.id, ctc_lpa=spec.ctc, deadline=now + timedelta(hours=30),
                         status=OfferStatus.pending, created_at=now - timedelta(hours=18)))
        elif (a.student_id in accept_for or taken < accepted) and a.student_id not in season.placed:
            db.add(Offer(application_id=a.id, ctc_lpa=spec.ctc, deadline=day + timedelta(days=2),
                         status=OfferStatus.accepted, created_at=day + timedelta(hours=12),
                         decided_at=day + timedelta(days=1)))
            season.placed[a.student_id] = (spec.ctc, spec.sector)
            taken += a.student_id not in accept_for
        else:
            db.add(Offer(application_id=a.id, ctc_lpa=spec.ctc, deadline=day + timedelta(days=2),
                         status=OfferStatus.declined, created_at=day + timedelta(hours=12),
                         decided_at=day + timedelta(days=1)))
    for a in oa_pass[:5]:
        db.add(Experience(student_id=a.student_id, drive_id=d.id, difficulty=rng.randint(2, 4),
                          topics=rng.sample(["dsa", "sql", "system design", "oops", "projects", "dbms",
                                             "networks", "puzzles", "computer architecture"], 3),
                          tips="Know your projects end to end; they asked follow-ups on trade-offs."))
    d.drive_start = day
    db.flush()


def _live_drive(db: Session, d: Drive, rng: random.Random, now: datetime, featured: Student,
                exclude: set[int]) -> None:
    rounds = _rounds(db, d, [
        ("Online test", RoundKind.test, 60, []),
        ("Technical interview", RoundKind.interview, 30, [
            ("Panel A", "Lecture Hall 101", "Priya Raman"), ("Panel B", "Lecture Hall 102", "Vikram Shenoy"),
            ("Panel C", "Lecture Hall 103", "Asha Kurian")]),
        ("HR interview", RoundKind.interview, 20, [("HR 1", "Placement Office", "Nisha Varghese")]),
    ])
    pool = [s for s in _eligible_students(db, d) if s.id != featured.id and s.id not in exclude]
    rng.shuffle(pool)
    apps = _apply(db, d, [featured, *pool[:29]], now - timedelta(days=6))
    for a in apps:
        a.status = AppStatus.in_process
    mine_app = next(a for a in apps if a.student_id == featured.id)
    oa_pass = [mine_app, *rng.sample([a for a in apps if a is not mine_app], k=min(14, len(apps) - 1))]
    for a in apps:
        if a in oa_pass:
            a.rounds_cleared = 1
        else:
            a.status, a.rejected_round = AppStatus.rejected, 1
    db.flush()

    start = (now - timedelta(minutes=75)).replace(second=0)
    start = start - timedelta(minutes=start.minute % 5)
    live.schedule_round(db, d, rounds[1], start, ADMIN, DEMO_HOURS)
    slots = list(db.scalars(select(Slot).where(Slot.round_id == rounds[1].id).order_by(Slot.start_at)))

    # Keep the featured student in the upcoming part of the queue: swap time and panel with a later slot.
    # (Swapping whole positions keeps every panel conflict-free and leaves the unique keys untouched.)
    mine = next(s for s in slots if s.application.student_id == featured.id)
    future = [s for s in slots if s.start_at > now + timedelta(minutes=20)]
    if mine.start_at <= now + timedelta(minutes=20) and future:
        target = future[0]
        mine.panel_id, target.panel_id = target.panel_id, mine.panel_id
        mine.start_at, target.start_at = target.start_at, mine.start_at
        mine.end_at, target.end_at = target.end_at, mine.end_at
        db.flush()
        slots.sort(key=lambda s: s.start_at)

    absent_left = True
    by_panel: dict[int, list[Slot]] = {}
    for s in slots:
        by_panel.setdefault(s.panel_id, []).append(s)
    passed: list[Application] = []
    for p_id, items in by_panel.items():
        items.sort(key=lambda s: s.start_at)
        t = items[0].start_at
        for s in items:
            if t + timedelta(minutes=30) <= now - timedelta(minutes=8):
                s.checked_in_at = s.start_at - timedelta(minutes=10)
                s.actual_start = max(t, s.start_at)
                s.actual_end = s.actual_start + timedelta(minutes=30 + rng.randint(-3, 9))
                s.status = SlotStatus.done
                ok = rng.random() < 0.55
                s.result = "pass" if ok else "fail"
                if ok:
                    s.application.rounds_cleared = 2
                    passed.append(s.application)
                else:
                    s.application.status, s.application.rejected_round = AppStatus.rejected, 2
                t = s.actual_end
            elif s.status == SlotStatus.scheduled and t <= now:
                # The interview in the room right now; Panel A is running long to create a real overrun.
                s.checked_in_at = s.start_at - timedelta(minutes=8)
                s.actual_start = t + (timedelta(minutes=14) if p_id == min(by_panel) else timedelta(0))
                s.actual_start = min(s.actual_start, now - timedelta(minutes=2))
                s.status = SlotStatus.in_progress
                t = s.actual_start + timedelta(minutes=30)
            elif s.application.student_id == featured.id or s.start_at < now + timedelta(minutes=50):
                overdue = s.start_at < now - timedelta(minutes=12)
                if absent_left and overdue and s.application.student_id != featured.id:
                    absent_left = False  # leave one late arrival so the inbox shows it
                    continue
                s.checked_in_at = now - timedelta(minutes=rng.randint(3, 25))
    d.status = DriveStatus.live
    db.flush()
    if passed:
        live.schedule_round(db, d, rounds[2], now + timedelta(minutes=5), ADMIN, DEMO_HOURS)
    live.maybe_propose_for_delay(db, d, "monitor", now)


def seed(reset: bool, count: int, seed_value: int) -> None:
    settings = get_settings()
    rng = random.Random(seed_value)
    if reset:
        Base.metadata.drop_all(bind=engine)
    create_all()
    upload_root = Path(settings.upload_dir)
    now = utcnow()

    with SessionLocal() as db:
        if db.scalar(select(Student.id).limit(1)) is not None:
            print("Database already has data. Run with --reset to rebuild the demo.")
            return
        _, cfg = active_policy(db)
        db.add_all([
            User(email=ADMIN, name="Placement Cell Admin", role=Role.admin),
            User(email="coordinator@nitc.ac.in", name="Placement Coordinator", role=Role.coordinator),
        ])
        db.flush()

        rows = _students(rng, count)
        master = SEED_DIR / "master_sheet.csv"
        _write_csv(master, rows)
        _write_csv(SEED_DIR / "new_batch_import.csv", _new_batch_csv(rng))
        report = importer.import_students(db, master.read_text(encoding="utf-8"))
        print(f"Imported {report.created} students from {master.name} ({len(report.errors)} errors)")

        students = list(db.scalars(select(Student).order_by(Student.id)))
        for s in students:
            track = "Software" if s.branch == "CSE" else "Core"
            rel = Path("resumes") / f"{s.id}_seed.pdf"
            _resume_pdf(upload_root / rel, s, track)
            db.add(Resume(student_id=s.id, track=track, filename=f"{s.roll_no}_resume.pdf", path=str(rel)))
        db.flush()
        for s in students:
            db.refresh(s)

        eashaan, midhun, harsh = (db.scalar(select(Student).where(Student.roll_no == f.roll)) for f in FEATURED)
        assert eashaan and midhun and harsh
        team = {eashaan.id, midhun.id, harsh.id}

        companies: dict[str, Company] = {}
        for c in CATALOG:
            slug = "".join(ch for ch in c.name.lower() if ch.isalnum())
            companies[c.name] = Company(name=c.name, sector=c.sector, contact_name=c.contact,
                                        contact_email=f"campus@{slug}.example", notes=c.about)
        db.add_all(companies.values())
        db.flush()

        # Completed drives are replayed oldest first so the season obeys the policy (P1, P3, P6).
        season = Season(cfg, {}, {})
        drives: dict[str, Drive] = {}
        for spec in sorted((c for c in CATALOG if c.status == "completed"), key=lambda c: -c.days):
            d = _drive(db, companies[spec.name], spec, now)
            drives[spec.name] = d
            _completed_drive(db, d, spec, rng, now, season, exclude=team,
                             accept_for={harsh.id} if spec.name == "Cognizant" else set(),
                             pending_for={midhun.id} if spec.name == "Accenture" else set(),
                             accepted=1 if spec.ctc >= cfg.freeze_at_ctc_lpa else 3)
            db.flush()
            for other in drives.values():  # accepted offers change everyone's eligibility for later drives
                if other.status != DriveStatus.completed:
                    eligibility.recompute_drive(db, other)

        for spec in (c for c in CATALOG if c.status != "completed"):
            d = _drive(db, companies[spec.name], spec, now)
            drives[spec.name] = d
            if spec.status == "live":
                _live_drive(db, d, rng, now, eashaan, team)
            elif spec.status in ("open", "closed"):
                _rounds(db, d, STANDARD_ROUNDS)
            elif spec.status == "draft":
                _rounds(db, d, STANDARD_ROUNDS[:2])

        for spec in (c for c in CATALOG if c.status in ("open", "closed")):
            d = drives[spec.name]
            share = 0.5 if spec.status == "closed" else 0.25
            pool = [s for s in _eligible_students(db, d) if s.id not in team]
            _apply(db, d, [s for s in pool if rng.random() < share], now - timedelta(hours=rng.randint(2, 60)))
        # The team's own applications.
        for name in ("Qualcomm", "NVIDIA"):
            if eashaan in _eligible_students(db, drives[name]):
                _apply(db, drives[name], [eashaan], now - timedelta(hours=20))
        if harsh in _eligible_students(db, drives["Intel"]):
            _apply(db, drives["Intel"], [harsh], now - timedelta(days=1))  # uses 1 of his 2 upgrade chances
        db.flush()

        # A student with one backlog disputes Qualcomm's no-backlog rule; it lands in the exceptions inbox.
        disputer = next((s for s in students if s.id not in team and s.backlogs == 1 and s.branch in CIRCUIT
                         and s.cgpa >= 7.5), None)
        if disputer is None:
            disputer = next(s for s in students if s.id not in team and s.branch == "ECE")
            disputer.backlogs = 1
        db.add(Dispute(student_id=disputer.id, drive_id=drives["Qualcomm"].id,
                       message="My only backlog was cleared in the supplementary exam last week and the result is "
                               "published. Please update my record."))
        for d in drives.values():
            if d.status in (DriveStatus.open, DriveStatus.draft, DriveStatus.closed):
                eligibility.recompute_drive(db, d)
        db.commit()

        base = settings.public_url
        lines = ["HireFlo demo links (valid for 21 days; rotate from the drive page).",
                 "CTCs are illustrative demo values, not actual offers.", ""]
        for name in ("AMD", "Qualcomm", "NVIDIA", "Intel", "KLA Tencor"):
            d = drives[name]
            db.refresh(d)
            lines.append(f"{name} recruiter: {base}/r/{create_link_token('recruiter', d.id, d.link_version)}")
            if d.status == DriveStatus.live:
                for r in d.rounds:
                    for p in r.panels:
                        lines.append(f"  {r.name} / {p.name}: "
                                     f"{base}/p/{create_link_token('panel', d.id, d.link_version, p.id)}")
        lines += ["", "Sign-ins (OTP is shown on screen in demo mode):",
                  f"  {ADMIN} (placement cell admin)", "  coordinator@nitc.ac.in (coordinator)"]
        lines += [f"  {f.email} ({f.name}: {f.note})" for f in FEATURED]
        lines.append(f"  {disputer.email} (backlog + open dispute)")
        (SEED_DIR / "demo_links.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        print(f"\nWrote {SEED_DIR / 'demo_links.txt'}")
        print(f"Live import demo sheet: {SEED_DIR / 'new_batch_import.csv'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the HireFlo (DriveOps) demo database.")
    parser.add_argument("--reset", action="store_true", help="drop all tables first")
    parser.add_argument("--students", type=int, default=180)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    seed(args.reset, max(30, args.students), args.seed)


if __name__ == "__main__":
    main()
