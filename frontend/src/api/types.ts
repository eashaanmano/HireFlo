// Response shapes returned by the DriveOps API.

export type Role = "student" | "coordinator" | "admin";
export type DriveStatus = "draft" | "open" | "closed" | "live" | "completed" | "cancelled";
export type AppStatus = "applied" | "in_process" | "rejected" | "selected" | "withdrawn";
export type SlotStatus = "scheduled" | "in_progress" | "done" | "no_show" | "cancelled";
export type OfferStatus = "pending" | "accepted" | "declined" | "revoked";

export interface Me {
  id: number;
  email: string;
  name: string;
  role: Role;
  student_id: number | null;
}

export interface DemoAccount {
  email: string;
  name: string;
  role: Role;
  note: string;
}

export interface Reason {
  code: string;
  ok: boolean;
  message: string;
  field: string | null;
  fixable: boolean;
}

export interface Student {
  id: number;
  roll_no: string;
  name: string;
  email: string;
  phone: string | null;
  branch: string;
  programme: string;
  cgpa: number;
  backlogs: number;
  grad_year: number;
  skills: string[];
  verified: boolean;
  archived: boolean;
}

export interface Drive {
  id: number;
  company_id: number;
  company: string;
  role: string;
  jd_text: string;
  ctc_lpa: number;
  tier: string;
  sector: string | null;
  location: string | null;
  status: DriveStatus;
  apply_deadline: string | null;
  drive_start: string | null;
  target_hires: number;
  skills: string[];
  applicants: number;
  eligible: number | null;
  rules_pending: number;
}

export interface Rule {
  id: number;
  field: "cgpa" | "backlogs" | "branch" | "programme" | "grad_year";
  op: ">=" | "<=" | "in";
  value: number | string[] | number[];
  source_text: string | null;
  confidence: number;
  approved: boolean;
  approved_by: string | null;
}

export interface Panel {
  id: number;
  round_id: number;
  name: string;
  room: string;
  interviewer: string | null;
}

export interface Round {
  id: number;
  name: string;
  kind: "test" | "interview";
  seq: number;
  duration_min: number;
  panels: Panel[];
}

export interface Application {
  id: number;
  drive_id: number;
  student_id: number;
  student: string;
  roll_no: string;
  branch: string;
  cgpa: number;
  status: AppStatus;
  stage: string;
  rounds_cleared: number;
  skill_match: number | null;
  offer_status: OfferStatus | null;
}

export interface Slot {
  id: number;
  round: string;
  round_id: number;
  panel: string;
  panel_id: number;
  room: string;
  student: string;
  student_id: number;
  roll_no: string;
  status: SlotStatus;
  start_at: string;
  end_at: string;
  projected_start: string | null;
  lateness_min: number;
  checked_in: boolean;
  result: "pass" | "fail" | null;
}

export interface Offer {
  id: number;
  application_id: number;
  company: string;
  role: string;
  ctc_lpa: number;
  tier: string;
  deadline: string;
  status: OfferStatus;
}

export interface FunnelStage {
  stage: string;
  count: number;
}

export interface ActionItem {
  kind: "offer" | "interview" | "apply" | "resume";
  title: string;
  detail: string;
  due: string | null;
  link: string;
}

export interface LiveItem {
  slot_id: number;
  company: string;
  round: string;
  panel: string;
  room: string;
  status: SlotStatus;
  checked_in: boolean;
  planned_start: string;
  eta: string;
  position: number | null;
  ahead: number;
}

export interface StudentDashboard {
  student: { name: string; roll_no: string; branch: string; programme: string };
  placement: {
    placed: boolean;
    tier: string | null;
    companies: string[];
    best_ctc_lpa: number | null;
    season_over: boolean;
    upgrade: { threshold_lpa: number; total: number; used: number; left: number } | null;
  };
  actions: ActionItem[];
  unread: number;
  funnel: FunnelStage[];
  insight: string;
  skills: {
    gaps: { skill: string; drives: number; of: number }[];
    strengths: { skill: string; drives: number }[];
    eligible_drives: number;
  };
  opportunity: {
    eligible_now: { id: number; label: string; tier: string }[];
    unlockable: { clear_backlogs: number; drives: string[] }[];
    lost_if_accept: { offer_id: number; company: string; tier: string; lost: string[] }[];
  };
  live: LiveItem[];
}

export interface StudentDriveEntry {
  drive: Drive;
  eligible: boolean | null;
  reasons: Reason[];
  application: Application | null;
}

export interface StudentDriveDetail extends StudentDriveEntry {
  rounds: { name: string; kind: string; seq: number; duration_min: number }[];
  dispute: { status: string; resolution: string | null } | null;
  experience: { count: number; avg_difficulty: number | null; topics: [string, number][] };
}

export interface Notification {
  id: number;
  title: string;
  body: string;
  link: string | null;
  read: boolean;
  created_at: string;
}

export interface Resume {
  id: number;
  track: string;
  filename: string;
  created_at: string;
}

export interface QaAnswer {
  answer: string;
  citations: { code: string; title: string; text: string }[];
  drives: number[];
  engine: string;
}

export interface InboxItem {
  kind: "dispute" | "replan" | "rules" | "absent" | "followup" | "jnf";
  id: number;
  drive_id: number;
  severity: "high" | "medium" | "low";
  title: string;
  detail: string;
  at: string;
}

export interface Company {
  id: number;
  name: string;
  sector: string | null;
  contact_name: string | null;
  contact_email: string | null;
  notes: string | null;
  archived: boolean;
  active_drives: number;
  live_drives: number;
}

export interface PolicyTier {
  name: string;
  min_ctc_lpa: number;
}

export interface PolicyConfig {
  tiers: PolicyTier[];
  upgrade_only: boolean;
  max_offers: number;
  freeze_at_ctc_lpa: number;
  freeze_sectors: string[];
  min_ctc_jump_lpa: number;
  offer_deadline_hours: number;
  upgrade_attempts: number;
}

export interface PolicyVersion {
  id: number;
  version: number;
  config: PolicyConfig;
  note: string | null;
  active: boolean;
  created_by: string | null;
  created_at: string;
}

export interface PolicyResponse {
  active: PolicyVersion;
  rules: { code: string; title: string; text: string }[];
  versions: PolicyVersion[];
}

export interface Proposal {
  id: number;
  reason: string;
  status: "pending" | "approved" | "dismissed" | "superseded";
  changes: {
    slot_id: number;
    student: string;
    round: string;
    from_panel: string;
    to_panel: string;
    from_start: string;
    to_start: string;
  }[];
  metrics: {
    max_lateness_before_min: number;
    max_lateness_after_min: number;
    finish_before: string;
    finish_after: string;
    saved_min: number;
  };
  created_at: string;
}

export interface ControlRoomPanel {
  id: number;
  name: string;
  room: string;
  round: string;
  round_id: number;
  duration_min: number;
  interviewer: string | null;
  done: number;
  now: Slot | null;
  queue: Slot[];
  late_min: number;
  finish: string | null;
  hold: { until: string | null } | null;
}

export interface ControlRoom {
  drive: { id: number; company: string; role: string; status: DriveStatus };
  now: string;
  panels: ControlRoomPanel[];
  proposals: Proposal[];
  no_shows: Slot[];
  progress: { done: number; checked_in: number; total: number };
}

export interface RecruiterOverview {
  drive: {
    id: number;
    company: string;
    role: string;
    status: DriveStatus;
    ctc_lpa: number;
    target_hires: number;
    tier: string;
    jd_text: string;
    skills: string[];
  };
  funnel: FunnelStage[];
  target: { hires: number; remaining_candidates: number; on_track: boolean };
  pending: { kind: string; title: string; detail: string; mine: boolean; round_id?: number }[];
  panels: {
    panel_id: number;
    name: string;
    room: string;
    round: string;
    remaining: number;
    done: number;
    avg_min: number | null;
    planned_min: number;
    finish: string | null;
    late_min: number;
  }[];
  offers: Record<OfferStatus, number>;
  rules: { field: string; op: string; value: unknown; approved: boolean; source_text: string | null }[];
  rounds: { id: number; name: string; kind: "test" | "interview"; seq: number; duration_min: number;
    panels: { id: number; name: string; room: string; interviewer: string | null }[] }[];
}

export interface PoolPreview {
  current: number;
  what_if: number;
  skill_match: Record<string, number>;
  total_students: number;
}

export interface PanelQueueCard extends Slot {
  cgpa: number;
  branch: string;
  programme: string;
  skills: string[];
  has_resume: boolean;
  application_id: number;
}

export interface PanelQueue {
  panel: { id: number; name: string; room: string; interviewer: string | null; round: string; duration_min: number };
  drive: { company: string; role: string; skills: string[] };
  current: PanelQueueCard | null;
  waiting: PanelQueueCard[];
  done: Slot[];
}

export interface ImportReport {
  created: number;
  updated: number;
  unchanged: number;
  errors: { row: number; roll_no: string | null; problems: string[] }[];
  columns: Record<string, string>;
  dry_run: boolean;
}

export interface SimMetrics {
  makespan_min: number;
  avg_wait_min: number;
  max_wait_min: number;
  panel_idle_min: number;
  replans: number;
}

export interface SimReport {
  config: Record<string, number>;
  static: SimMetrics;
  replanned: SimMetrics;
  improvement: { makespan_pct: number; avg_wait_pct: number; panel_idle_pct: number };
}

export interface AuditEvent {
  id: number;
  ts: string;
  type: string;
  actor: string;
  entity: string | null;
  entity_id: number | null;
  drive_id: number | null;
  payload: Record<string, unknown>;
}

export interface Stats {
  students: number;
  placed: number;
  placed_pct: number;
  offers: number;
  active_drives: number;
  by_branch: { branch: string; students: number; placed: number }[];
  by_tier: { tier: string; placed: number }[];
}

export interface Coverage {
  unplaced: number;
  uncovered: number;
  by_branch: { branch: string; students: number }[];
  students: { id: number; name: string; branch: string; cgpa: number; backlogs: number }[];
}

export interface AtRisk {
  id: number;
  name: string;
  branch: string;
  cgpa: number;
  reason: string;
  suggestion: string;
}

export interface Paged<T> {
  total: number;
  page: number;
  per_page: number;
  items: T[];
}

export interface CellUser {
  id: number;
  email: string;
  name: string;
  role: Role;
  active: boolean;
}

export interface DisputeRow {
  id: number;
  student: string;
  student_id: number;
  roll_no: string;
  drive_id: number;
  drive: string;
  message: string;
  status: "open" | "upheld" | "rejected";
  resolution: string | null;
  reasons: Reason[];
  created_at: string;
}

// ---------------------------------------------------------------- analytics
export interface StudentAnalytics {
  cgpa: number;
  cutoffs: { drive_id: number; company: string; role: string; ctc_lpa: number; tier: string; cutoff: number | null; eligible: boolean }[];
  weekly_applications: { week: string; count: number }[];
  by_tier: { tier: string; eligible: number; total: number }[];
  pass_rates: { stage: string; you: number | null; batch: number | null }[];
  eligible_ctc: { count: number; median: number | null; max: number | null };
}

export interface RecruiterAnalytics {
  totals: { applicants: number; shortlisted: number; selected: number; offers_accepted: number; avg_cgpa: number | null; median_skill_match: number | null };
  branches: { branch: string; applicants: number; shortlisted: number; selected: number }[];
  cgpa_histogram: { bucket: string; count: number }[];
  skill_coverage: { skill: string; pct: number }[];
  daily: { date: string; applications: number }[];
  panels: { panel: string; pass: number; fail: number; avg_min: number | null; planned_min: number }[];
  funnel: { stage: string; count: number }[];
}

export interface SeasonAnalytics {
  kpis: { offers_accepted: number; offers_pending: number; median_ctc: number | null; max_ctc: number | null; season_over: number; still_in_play: number; psu_offers: number };
  offers_by_week: { week: string; accepted: number; cumulative: number }[];
  ctc_hist: { band: string; offers: number }[];
  by_tier: { tier: string; students: number }[];
  top_recruiters: { company: string; offers: number }[];
  drives: { id: number; company: string; role: string; ctc_lpa: number; tier: string; sector: string; status: string; deadline: string | null; drive_day: string | null; eligible: number; applied: number; selected: number }[];
}

// ---------------------------------------------------------------- live disruptions
export type DisruptionKind = "overrun" | "panel_down" | "no_show" | "release";

export interface DisruptResult {
  disruption: string;
  kind: DisruptionKind;
  solve_ms: number;
  proposal: number | null;
  changes: Proposal["changes"];
  metrics: Proposal["metrics"] | null;
  applied: number;
}

// ---------------------------------------------------------------- offer decision simulator
export interface OfferWhatIf {
  offer: { id: number; company: string; role: string; ctc_lpa: number; tier: string; sector: string | null; ends_season: boolean; deadline: string | null };
  verdict: string;
  threshold_lpa: number;
  chances: { total: number; left_after_accept: number };
  drives: {
    drive_id: number; company: string; role: string; ctc_lpa: number; tier: string;
    eligible_now: boolean; eligible_after: boolean; uses_chance: boolean; applied: boolean; odds: number; reason: string | null;
  }[];
  summary: {
    keep: number; lose: number;
    accept_upgrade_odds: number; accept_upgrade_targets: string[];
    decline_better_odds: number; decline_better_targets: string[];
  };
  basis: { tier_rates: Record<string, number>; personal_factor: number; method: string };
}

// ---------------------------------------------------------------- fairness audit
export interface FairnessAudit {
  drive: { id: number; company: string; role: string; ctc_lpa: number };
  totals: { students: number; policy_excluded: number; considered: number; eligible: number; rule_excluded: number };
  rules: { id: number | null; field: string; label: string; excluded: number; sole: number; by_branch: { branch: string; count: number }[] }[];
  branches: { branch: string; students: number; eligible: number; rate: number; ratio: number | null }[];
  near_miss: { student_id: number; name: string; roll_no: string; branch: string; cgpa: number; gap: number }[];
  near_miss_margin: number;
  sweep: { cutoff: number; eligible: number; current: boolean }[];
  flags: { level: "warn" | "info" | "ok"; text: string }[];
}

// ---------------------------------------------------------------- enhancements: forecast, company profiles, retrospective
export interface PlacementForecast {
  branches: { branch: string; students: number; placed: number; forecast_placed: number; forecast_pct: number; placed_pct: number }[];
  totals: { students: number; placed: number; forecast_placed: number; forecast_pct: number };
  basis: { tier_rates: Record<string, number>; default_rate: number; upcoming_drives: number; method: string };
}

export interface EarlyWarning {
  id: number;
  name: string;
  roll_no: string;
  branch: string;
  cgpa: number;
  backlogs: number;
  chance: number;
  eligible_upcoming: number;
  reasons: string[];
  suggestion: string | null;
  severity: "high" | "medium";
}

export interface CompanyProfile {
  company: { id: number; name: string; sector: string | null };
  has_history: boolean;
  seasons: string[];
  drives: number;
  roles: string[];
  ctc: { min: number; max: number } | null;
  tiers: string[];
  rounds: { name: string; kind: string; duration_min: number }[];
  branches_hired: { branch: string; selected: number }[];
  stages: { stage: string; count: number; rate: number | null }[];
  selection_rate: number | null;
}

export interface Retrospective {
  season: string;
  generated_at: string;
  available_seasons: string[];
  summary: { students: number; placed: number; placed_pct: number; offers_accepted: number; companies: number; drives: number; median_ctc: number | null; max_ctc: number | null };
  by_branch: { branch: string; students: number; placed: number; placed_pct: number; median_ctc: number | null }[];
  by_tier: { tier: string; offers: number }[];
  drive_day: { interviews: number; no_shows: number; avg_delay_min: number | null; on_time_pct: number | null; replans_applied: number; panel_disruptions: number };
  trends: { season: string; offers: number; students_placed: number; median_ctc: number | null }[];
}

// ---------------------------------------------------------------- AI assistant
export interface PolicyRule {
  code: string;
  title: string;
  text: string;
}

export interface LlmStatus {
  configured: { name: string; model: string }[];
  routing: Record<string, string[]>;
  assistant_enabled: boolean;
}

export interface LlmTest {
  provider: string;
  ok: boolean;
  ms: number;
  reply?: string;
  error?: string;
}

// ---------------------------------------------------------------- company job notification form (JNF)
export type JnfStatus = "invited" | "submitted" | "changes_requested" | "approved" | "declined";

export interface JnfRound {
  name: string;
  kind: "test" | "interview";
  duration_min: number;
}

export interface JnfData {
  role: string;
  ctc_lpa: number;
  base_lpa: number | null;
  variable_lpa: number | null;
  joining_bonus_lpa: number | null;
  other_lpa: number | null;
  location: string | null;
  bond_months: number;
  bond_note: string | null;
  branches: string[];
  programmes: string[];
  cgpa_min: number | null;
  max_backlogs: number | null;
  rounds: JnfRound[];
  interview_panels: number | null;
  preferred_date: string | null;
  apply_by: string | null;
  target_hires: number;
  jd_text: string;
  notes: string | null;
}

export interface JnfCriteria {
  ctc_lpa: number;
  branches: string[];
  programmes: string[];
  cgpa_min: number | null;
  max_backlogs: number | null;
}

export interface JnfPool {
  eligible: number;
  total_students: number;
  tier: string;
}

export interface JnfPublic {
  id: number;
  status: JnfStatus;
  company_name: string;
  contact_name: string | null;
  data: JnfData | null;
  cell_note: string | null;
  editable: boolean;
  options: { branches: string[]; programmes: string[] };
}

export interface JnfRow {
  id: number;
  company_name: string;
  contact_name: string | null;
  contact_email: string | null;
  status: JnfStatus;
  role: string | null;
  ctc_lpa: number | null;
  drive_id: number | null;
  cell_note: string | null;
  invited_by: string;
  decided_by: string | null;
  created_at: string | null;
  submitted_at: string | null;
  decided_at: string | null;
}

export interface JnfDetail {
  jnf: JnfRow & { data: JnfData | null };
  review: (JnfPool & { flags: string[] }) | null;
  url: string;
}
