"""Resume files on disk, and the fictional PDFs the demo seed draws for every student.

Free hosts such as Render wipe the local disk on every restart while the database (Neon) keeps its rows. A seeded
resume is drawn from the student record, so when its file is missing it is drawn again on first use. Resumes that
people uploaded can't be rebuilt; those stay missing until they are uploaded again.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ..core.config import get_settings
from ..models import Resume, Student

SEED_SUFFIX = "_seed.pdf"


def seed_resume_path(student_id: int) -> Path:
    """Path, relative to the upload folder, of the seeded resume for this student."""
    return Path("resumes") / f"{student_id}{SEED_SUFFIX}"


def draw_demo_resume(path: Path, s: Student, track: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4
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


def resume_file(r: Resume, upload_root: Path | None = None) -> Path:
    """The resume's file, redrawing a seeded one that the host's disk lost. The path may still not exist."""
    root = upload_root if upload_root is not None else Path(get_settings().upload_dir)
    path = root / r.path
    if not path.is_file() and Path(r.path).name.endswith(SEED_SUFFIX):
        draw_demo_resume(path, r.student, r.track)
    return path
