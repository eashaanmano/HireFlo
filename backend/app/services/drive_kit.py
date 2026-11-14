"""Printable drive-day kit: a cover sheet, one run sheet per panel (with the panel link as a QR code) and an
attendance sheet. It is the paper fallback when the network fails on drive day.

The venue check-in QR is deliberately not printed: it rotates every 90 seconds so a photo of it cannot be
shared, and a printed copy would stop working almost at once. Without the network the cell checks students in
from the attendance sheet instead.
"""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.security import create_link_token
from ..core.timeutil import ensure_utc, utcnow
from ..models import Drive, Slot, SlotStatus

SHOWN = (SlotStatus.scheduled, SlotStatus.in_progress, SlotStatus.done, SlotStatus.no_show)


def _local(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    t = ensure_utc(dt)
    assert t is not None
    return (t + timedelta(minutes=get_settings().display_utc_offset_min)).strftime("%H:%M")


def _day(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    t = ensure_utc(dt)
    assert t is not None
    return (t + timedelta(minutes=get_settings().display_utc_offset_min)).strftime("%a %d %b %Y")


def kit_pdf(db: Session, drive: Drive) -> bytes:
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    base = get_settings().public_url
    slots = list(db.scalars(select(Slot).where(Slot.drive_id == drive.id, Slot.status.in_(SHOWN))
                            .order_by(Slot.start_at)))
    by_panel: dict[int, list[Slot]] = defaultdict(list)
    for s in slots:
        by_panel[s.panel_id].append(s)
    panels = [(r, p) for r in drive.rounds for p in r.panels]
    title = f"{drive.company.name}: {drive.role}"

    def grid(head: list[str], rows: list[list[object]], widths: list[float]) -> Table:
        t = Table([head] + rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9d9d9")),
                               ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                               ("FONTSIZE", (0, 0), (-1, -1), 9),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("TOPPADDING", (0, 1), (-1, -1), 6), ("BOTTOMPADDING", (0, 1), (-1, -1), 6)]))
        return t

    def qr(url: str, size: float = 4 * cm) -> Drawing:
        w = QrCodeWidget(url)
        x0, y0, x1, y1 = w.getBounds()
        dr = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
        dr.add(w)
        return dr

    story: list[object] = [
        Paragraph(f"Drive-day kit: {title}", styles["Title"]),
        Paragraph(f"{_day(drive.drive_start or (slots[0].start_at if slots else None))} &nbsp;·&nbsp; "
                  f"{drive.ctc_lpa:g} LPA &nbsp;·&nbsp; printed {_day(utcnow())} {_local(utcnow())}", styles["Normal"]),
        Spacer(1, 10),
        grid(["Round", "Panel", "Room", "Interviewer", "Interviews", "First", "Last"],
             [[r.name, p.name, p.room, p.interviewer or "-", len(by_panel[p.id]),
               _local(by_panel[p.id][0].start_at) if by_panel[p.id] else "-",
               _local(by_panel[p.id][-1].end_at) if by_panel[p.id] else "-"] for r, p in panels]
             or [["-", "-", "-", "-", 0, "-", "-"]],
             [3.2 * cm, 2.4 * cm, 2.8 * cm, 3 * cm, 2 * cm, 1.6 * cm, 1.6 * cm]),
        Spacer(1, 12),
        Paragraph("If the network fails: interviewers keep using their sheet and record pass / fail by hand; the "
                  "cell marks arrivals on the attendance sheet and enters both in HireFlo once it is back. The venue "
                  "check-in QR is not printed because it changes every 90 seconds.", styles["Normal"]),
    ]
    for r, p in panels:
        story += [PageBreak(), Paragraph(f"{p.name} · {p.room}", styles["Title"]),
                  Paragraph(f"{title} &nbsp;·&nbsp; {r.name} &nbsp;·&nbsp; interviewer: {p.interviewer or '-'}",
                            styles["Normal"]), Spacer(1, 8)]
        url = f"{base}/p/{create_link_token('panel', drive.id, drive.link_version, p.id)}"
        story += [Table([[qr(url), Paragraph("Scan to open this panel's screen: next candidate, resume and "
                                              "Start / Done. Keep this sheet private: the code opens the panel view "
                                              "for this drive.", styles["Normal"])]],
                        colWidths=[4.6 * cm, 12 * cm], hAlign="LEFT"), Spacer(1, 10)]
        rows = [[_local(s.start_at), s.application.student.roll_no, s.application.student.name,
                 s.application.student.branch, "", ""] for s in by_panel[p.id]]
        story.append(grid(["Time", "Roll no", "Name", "Branch", "Arrived", "Result"],
                          rows or [["-", "-", "No interviews scheduled", "-", "", ""]],
                          [1.6 * cm, 2.8 * cm, 5.4 * cm, 1.8 * cm, 2.2 * cm, 2.6 * cm]))
    people: dict[int, Slot] = {}
    for s in slots:
        people.setdefault(s.application.student_id, s)
    att = sorted(people.values(), key=lambda s: s.application.student.roll_no)
    story += [PageBreak(), Paragraph(f"Attendance: {title}", styles["Title"]), Spacer(1, 6),
              grid(["#", "Roll no", "Name", "Branch", "First slot", "Time in", "Signature"],
                   [[i + 1, s.application.student.roll_no, s.application.student.name, s.application.student.branch,
                     _local(s.start_at), "", ""] for i, s in enumerate(att)] or [["-", "-", "No candidates", "", "", "",
                                                                                    ""]],
                   [0.9 * cm, 2.6 * cm, 4.8 * cm, 1.6 * cm, 1.9 * cm, 1.8 * cm, 3.2 * cm])]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.6 * cm,
                      bottomMargin=1.6 * cm, title=f"Drive-day kit {title}").build(story)
    return buf.getvalue()
