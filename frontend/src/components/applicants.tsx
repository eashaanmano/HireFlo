import type { Application } from "../api/types";
import { cellText, type Column } from "./DataTable";
import { Badge } from "./ui";
import { BarChart } from "./viz";

export const APPLICANT_COLUMNS: Column<Application>[] = [
  { key: "student", header: "Student", value: (a) => a.student, pinned: true,
    render: (a) => <><strong>{a.student}</strong><div className="small faint">{a.roll_no}</div></> },
  { key: "roll_no", header: "Roll no", value: (a) => a.roll_no, hidden: true },
  { key: "branch", header: "Branch", value: (a) => a.branch, filter: true },
  { key: "cgpa", header: "CGPA", value: (a) => a.cgpa, align: "right", render: (a) => a.cgpa.toFixed(2) },
  { key: "skill_match", header: "Skill match", value: (a) => (a.skill_match === null ? null : Math.round(a.skill_match * 100)), align: "right",
    render: (a) => (a.skill_match === null ? "—" : (
      <span className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
        <span className="mini-track"><i style={{ width: `${Math.round(a.skill_match * 100)}%` }} /></span>{Math.round(a.skill_match * 100)}%
      </span>
    )) },
  { key: "stage", header: "Stage", value: (a) => a.stage, filter: true,
    render: (a) => <Badge tone={a.status === "rejected" ? "bad" : a.status === "selected" ? "ok" : "info"}>{a.stage}</Badge> },
  { key: "offer", header: "Offer", value: (a) => a.offer_status ?? "", filter: true, hidden: true },
];

/** Applicants per stage as bars; clicking a bar filters the applicant table to that stage. */
export function StageBars({ apps, selected, onSelect }: { apps: Application[]; selected: string | null; onSelect: (s: string | null) => void }) {
  const counts = new Map<string, number>();
  apps.forEach((a) => counts.set(a.stage, (counts.get(a.stage) ?? 0) + 1));
  const data = [...counts.entries()].map(([label, value]) => ({
    label, value,
    color: /reject/i.test(label) ? "var(--red)" : /select|offer/i.test(label) ? "var(--green)" : "var(--signal)",
  }));
  if (data.length < 2) return null;
  return (
    <BarChart data={data} height={150} total={apps.length} selected={selected}
      onSelect={(d) => onSelect(selected === d.label ? null : cellText(d.label))} />
  );
}
