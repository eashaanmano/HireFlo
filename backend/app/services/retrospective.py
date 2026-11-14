"""Season retrospective report (SRS FUT-5, FUT-6): offers by branch and tier, CTC statistics, drive-day
performance and trends against earlier seasons, exportable as CSV and PDF."""
from __future__ import annotations

import io
import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.timeutil import ensure_utc, minutes_between, utcnow
from ..models import (
    Drive,
    DriveStatus,
    Event,
    Offer,
    OfferStatus,
    ProposalStatus,
    ReplanProposal,
    Slot,
    SlotStatus,
    Student,
)
from .company_profile import drive_season, season_of
from .exports import rows_csv
from .policy import active_policy

COUNTED = (DriveStatus.open, DriveStatus.closed, DriveStatus.live, DriveStatus.completed)


def seasons(db: Session) -> list[str]:
    found = {drive_season(d) for d in db.scalars(select(Drive).where(Drive.status.in_(COUNTED)))}
    return sorted(s for s in found if s)


def _season_drives(db: Session, season: str) -> list[Drive]:
    return [d for d in db.scalars(select(Drive).where(Drive.status.in_(COUNTED))) if drive_season(d) == season]


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 2) if xs else None


def _accepted(drives: list[Drive]) -> list[Offer]:
    ids = {d.id for d in drives}
    return [a.offer for d in drives for a in d.applications
            if a.offer is not None and a.offer.status == OfferStatus.accepted and a.drive_id in ids]


def season_report(db: Session, season: str | None = None) -> dict[str, Any]:
    _, cfg = active_policy(db)
    all_seasons = seasons(db)
    season = season or (all_seasons[-1] if all_seasons else season_of(utcnow()))
    drives = _season_drives(db, season or "")
    accepted = _accepted(drives)
    students = list(db.scalars(select(Student).where(Student.archived.is_(False))))

    best: dict[int, Offer] = {}
    for o in accepted:
        sid = o.application.student_id
        if sid not in best or o.ctc_lpa > best[sid].ctc_lpa:
            best[sid] = o

    by_branch: dict[str, dict[str, Any]] = defaultdict(lambda: {"students": 0, "placed": 0, "ctcs": []})
    for s in students:
        by_branch[s.branch]["students"] += 1
        if s.id in best:
            by_branch[s.branch]["placed"] += 1
            by_branch[s.branch]["ctcs"].append(best[s.id].ctc_lpa)
    branches = [{"branch": b, "students": v["students"], "placed": v["placed"],
                 "placed_pct": round(100 * v["placed"] / v["students"], 1) if v["students"] else 0.0,
                 "median_ctc": _median(v["ctcs"])}
                for b, v in sorted(by_branch.items(), key=lambda kv: -kv[1]["students"])]

    tiers: dict[str, int] = {t.name: 0 for t in cfg.tiers}
    tiers["PSU"] = 0
    for o in accepted:
        sector = (o.application.drive.company.sector or "").upper()
        tiers["PSU" if sector == "PSU" else cfg.tier_name(o.ctc_lpa)] += 1

    drive_ids = [d.id for d in drives]
    slots = list(db.scalars(select(Slot).where(Slot.drive_id.in_(drive_ids)))) if drive_ids else []
    delays = [max(0.0, minutes_between(ensure_utc(s.start_at), ensure_utc(s.actual_start)))  # type: ignore[arg-type]
              for s in slots if s.status == SlotStatus.done and s.actual_start is not None]
    replans = holds = 0
    if drive_ids:
        replans = len(db.scalars(select(ReplanProposal).where(ReplanProposal.drive_id.in_(drive_ids),
                                                              ReplanProposal.status == ProposalStatus.approved)).all())
        holds = len(db.scalars(select(Event).where(Event.drive_id.in_(drive_ids), Event.type == "panel.hold")).all())

    trends = []
    for s_label in all_seasons:
        acc = _accepted(_season_drives(db, s_label))
        placed = {o.application.student_id for o in acc}
        trends.append({"season": s_label, "offers": len(acc), "students_placed": len(placed),
                       "median_ctc": _median([o.ctc_lpa for o in acc])})

    ctcs = [o.ctc_lpa for o in best.values()]
    return {
        "season": season,
        "generated_at": utcnow().isoformat(),
        "summary": {
            "students": len(students), "placed": len(best),
            "placed_pct": round(100 * len(best) / len(students), 1) if students else 0.0,
            "offers_accepted": len(accepted), "companies": len({d.company_id for d in drives}), "drives": len(drives),
            "median_ctc": _median(ctcs), "max_ctc": max(ctcs) if ctcs else None,
        },
        "by_branch": branches,
        "by_tier": [{"tier": t, "offers": n} for t, n in tiers.items()],
        "drive_day": {
            "interviews": sum(s.status == SlotStatus.done for s in slots),
            "no_shows": sum(s.status == SlotStatus.no_show for s in slots),
            "avg_delay_min": round(statistics.fmean(delays), 1) if delays else None,
            "on_time_pct": round(100 * sum(d <= 5 for d in delays) / len(delays), 1) if delays else None,
            "replans_applied": replans, "panel_disruptions": holds,
        },
        "trends": trends,
    }


def report_csv(r: dict[str, Any]) -> str:
    rows: list[list[object]] = [["summary", k, v] for k, v in r["summary"].items()]
    for b in r["by_branch"]:
        rows.append(["branch", b["branch"],
                     f'{b["placed"]}/{b["students"]} placed ({b["placed_pct"]}%), median CTC {b["median_ctc"]}'])
    rows += [["tier", t["tier"], t["offers"]] for t in r["by_tier"]]
    rows += [["drive_day", k, v] for k, v in r["drive_day"].items()]
    for t in r["trends"]:
        rows.append(["trend", t["season"],
                     f'{t["offers"]} offers, {t["students_placed"]} placed, median CTC {t["median_ctc"]}'])
    return rows_csv(["section", "item", "value"], rows)


def _dash(v: object) -> object:
    return "-" if v is None else v


def report_pdf(r: dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2 * cm, title=f"Season Retrospective {r['season']}")

    def table(head: list[str], body: list[list[object]]) -> Table:
        t = Table([head] + body, hAlign="LEFT")
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9d9d9")),
                               ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                               ("FONTSIZE", (0, 0), (-1, -1), 9)]))
        return t

    def heading(text: str) -> list[object]:
        return [Spacer(1, 10), Paragraph(text, styles["Heading2"])]

    s, dd = r["summary"], r["drive_day"]
    summary = [["Students", s["students"]], ["Placed", f'{s["placed"]} ({s["placed_pct"]}%)'],
               ["Offers accepted", s["offers_accepted"]], ["Companies / drives", f'{s["companies"]} / {s["drives"]}'],
               ["Median CTC (LPA)", _dash(s["median_ctc"])], ["Highest CTC (LPA)", _dash(s["max_ctc"])]]
    branches = [[b["branch"], b["students"], b["placed"], b["placed_pct"], _dash(b["median_ctc"])]
                for b in r["by_branch"]]
    on_time = f'{dd["on_time_pct"]}%' if dd["on_time_pct"] is not None else "-"
    drive_day = [["Interviews held", dd["interviews"]], ["No-shows", dd["no_shows"]],
                 ["Average start delay (min)", _dash(dd["avg_delay_min"])],
                 ["Interviews within 5 min of plan", on_time],
                 ["Re-plans applied", dd["replans_applied"]], ["Panel disruptions", dd["panel_disruptions"]]]
    trends = [[t["season"], t["offers"], t["students_placed"], _dash(t["median_ctc"])] for t in r["trends"]]
    story = [Paragraph(f"Season Retrospective Report {r['season']}", styles["Title"]),
             Paragraph("NIT Calicut placement season, generated by HireFlo", styles["Normal"]),
             *heading("Summary"), table(["Measure", "Value"], summary),
             *heading("Placements by branch"),
             table(["Branch", "Students", "Placed", "Placed %", "Median CTC"], branches),
             *heading("Offers by tier"), table(["Tier", "Offers"], [[t["tier"], t["offers"]] for t in r["by_tier"]]),
             *heading("Drive-day performance"), table(["Measure", "Value"], drive_day),
             *heading("Trends across seasons"),
             table(["Season", "Offers", "Students placed", "Median CTC"], trends or [["-", 0, 0, "-"]])]
    doc.build(story)
    return buf.getvalue()
