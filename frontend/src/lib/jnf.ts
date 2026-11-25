// The job notification form keeps numbers as text while it is being typed, so a field can be empty; these helpers
// turn that draft into what the API takes, and a saved form back into a draft.
import type { JnfCriteria, JnfData, JnfStatus } from "../api/types";

export interface JnfDraftRound {
  name: string;
  kind: "test" | "interview";
  duration_min: string;
}

export interface JnfDraft {
  role: string;
  ctc_lpa: string;
  base_lpa: string;
  variable_lpa: string;
  joining_bonus_lpa: string;
  other_lpa: string;
  location: string;
  bond_months: string;
  bond_note: string;
  branches: string[];
  programmes: string[];
  cgpa_min: string;
  max_backlogs: string;
  rounds: JnfDraftRound[];
  interview_panels: string;
  preferred_date: string;
  apply_by: string;
  target_hires: string;
  jd_text: string;
  notes: string;
}

export const JNF_STATUS: Record<JnfStatus, { label: string; tone?: "ok" | "warn" | "bad" | "info" }> = {
  invited: { label: "Link sent" },
  submitted: { label: "To review", tone: "warn" },
  changes_requested: { label: "Changes asked", tone: "info" },
  approved: { label: "Approved", tone: "ok" },
  declined: { label: "Declined", tone: "bad" },
};

export function blankJnf(): JnfDraft {
  return {
    role: "", ctc_lpa: "", base_lpa: "", variable_lpa: "", joining_bonus_lpa: "", other_lpa: "", location: "",
    bond_months: "0", bond_note: "", branches: [], programmes: [], cgpa_min: "", max_backlogs: "",
    rounds: [
      { name: "Online test", kind: "test", duration_min: "60" },
      { name: "Technical interview", kind: "interview", duration_min: "30" },
      { name: "HR interview", kind: "interview", duration_min: "20" },
    ],
    interview_panels: "", preferred_date: "", apply_by: "", target_hires: "1", jd_text: "", notes: "",
  };
}

function num(v: string): number | null {
  if (v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function text(v: string): string | null {
  return v.trim() === "" ? null : v.trim();
}

function codes(values: string[]): string[] {
  return [...new Set(values.map((v) => v.trim().toUpperCase()).filter(Boolean))];
}

const str = (n: number | null | undefined) => (n === null || n === undefined ? "" : String(n));

export function jnfPayload(d: JnfDraft): JnfData {
  return {
    role: d.role.trim(),
    ctc_lpa: num(d.ctc_lpa) ?? 0,
    base_lpa: num(d.base_lpa),
    variable_lpa: num(d.variable_lpa),
    joining_bonus_lpa: num(d.joining_bonus_lpa),
    other_lpa: num(d.other_lpa),
    location: text(d.location),
    bond_months: num(d.bond_months) ?? 0,
    bond_note: text(d.bond_note),
    branches: codes(d.branches),
    programmes: codes(d.programmes),
    cgpa_min: num(d.cgpa_min),
    max_backlogs: num(d.max_backlogs),
    rounds: d.rounds.map((r) => ({ name: r.name.trim(), kind: r.kind, duration_min: num(r.duration_min) ?? 0 })),
    interview_panels: num(d.interview_panels),
    preferred_date: text(d.preferred_date),
    apply_by: text(d.apply_by),
    target_hires: num(d.target_hires) ?? 1,
    jd_text: d.jd_text.trim(),
    notes: text(d.notes),
  };
}

export function draftFrom(data: JnfData | null): JnfDraft {
  if (!data) return blankJnf();
  return {
    role: data.role, ctc_lpa: str(data.ctc_lpa), base_lpa: str(data.base_lpa), variable_lpa: str(data.variable_lpa),
    joining_bonus_lpa: str(data.joining_bonus_lpa), other_lpa: str(data.other_lpa), location: data.location ?? "",
    bond_months: str(data.bond_months), bond_note: data.bond_note ?? "", branches: data.branches,
    programmes: data.programmes, cgpa_min: str(data.cgpa_min), max_backlogs: str(data.max_backlogs),
    rounds: data.rounds.map((r) => ({ name: r.name, kind: r.kind, duration_min: str(r.duration_min) })),
    interview_panels: str(data.interview_panels), preferred_date: data.preferred_date ?? "",
    apply_by: data.apply_by ?? "", target_hires: str(data.target_hires), jd_text: data.jd_text,
    notes: data.notes ?? "",
  };
}

/** The part of the form that decides who can apply; null until there is a CTC to place it in a tier. */
export function criteriaOf(d: JnfDraft): JnfCriteria | null {
  const ctc = num(d.ctc_lpa);
  if (ctc === null || ctc <= 0) return null;
  return { ctc_lpa: ctc, branches: codes(d.branches), programmes: codes(d.programmes), cgpa_min: num(d.cgpa_min), max_backlogs: num(d.max_backlogs) };
}
