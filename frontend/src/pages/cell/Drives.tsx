import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { Company, Drive } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { Badge, Button, Card, Empty, ErrorBox, Field, Loading, Modal, PageHead, useAction } from "../../components/ui";
import { dateTime, fromLocalInput, lpa, toLocalInput } from "../../lib/format";

const TONE: Record<string, "ok" | "warn" | "bad" | "info" | undefined> = {
  live: "warn",
  open: "ok",
  draft: "info",
  cancelled: "bad",
};

function NewDrive({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const companies = useQuery({ queryKey: ["cell", "companies"], queryFn: () => api.get<Company[]>("/api/cell/companies") });
  const [form, setForm] = useState({
    company_id: 0,
    role: "",
    ctc_lpa: 10,
    location: "",
    target_hires: 3,
    deadline: toLocalInput(new Date(Date.now() + 5 * 86400_000)),
    jd_text: "",
  });
  const create = useAction(
    () =>
      api.post<Drive>("/api/cell/drives", {
        company_id: form.company_id,
        role: form.role,
        ctc_lpa: form.ctc_lpa,
        location: form.location || null,
        target_hires: form.target_hires,
        apply_deadline: fromLocalInput(form.deadline),
        jd_text: form.jd_text,
      }),
    { success: "Drive created. Review the extracted rules next.", invalidate: [["cell"]], onDone: (d) => navigate(`/cell/drives/${d.id}`) },
  );
  const set = (k: keyof typeof form, v: string | number) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <Modal title="New drive" onClose={onClose}>
      <div className="stack">
        <div className="form-grid">
          <Field label="Company">
            <select value={form.company_id} onChange={(e) => set("company_id", Number(e.target.value))}>
              <option value={0}>Choose…</option>
              {companies.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          <Field label="Role"><input value={form.role} onChange={(e) => set("role", e.target.value)} /></Field>
          <Field label="CTC (LPA)"><input type="number" min={1} step={0.5} value={form.ctc_lpa} onChange={(e) => set("ctc_lpa", Number(e.target.value))} /></Field>
          <Field label="Target hires"><input type="number" min={1} value={form.target_hires} onChange={(e) => set("target_hires", Number(e.target.value))} /></Field>
          <Field label="Location"><input value={form.location} onChange={(e) => set("location", e.target.value)} /></Field>
          <Field label="Applications close"><input type="datetime-local" value={form.deadline} onChange={(e) => set("deadline", e.target.value)} /></Field>
        </div>
        <Field label="Job description (eligibility rules are extracted from it)">
          <textarea value={form.jd_text} onChange={(e) => set("jd_text", e.target.value)} placeholder="Paste the JD here" />
        </Field>
        <div className="row">
          <Button variant="primary" busy={create.isPending} disabled={!form.company_id || form.role.length < 2} onClick={() => create.mutate(undefined)}>
            Create drive
          </Button>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </Modal>
  );
}

const takeUp = (d: Drive) => (d.eligible ? Math.round((100 * d.applicants) / d.eligible) : null);

const DRIVE_COLUMNS: Column<Drive>[] = [
  { key: "company", header: "Drive", value: (d) => d.company, pinned: true,
    render: (d) => (
      <>
        <Link to={`/cell/drives/${d.id}`}><strong>{d.company}</strong></Link>
        <div className="small muted">{d.role}</div>
        {d.rules_pending > 0 && <div className="small" style={{ color: "var(--amber)" }}>{d.rules_pending} rule(s) to approve</div>}
      </>
    ) },
  { key: "role", header: "Role", value: (d) => d.role, hidden: true },
  { key: "ctc", header: "CTC (LPA)", value: (d) => d.ctc_lpa, align: "right",
    render: (d) => <>{lpa(d.ctc_lpa)}<div className="small faint">{d.tier}{d.sector === "PSU" ? ", PSU" : ""}</div></> },
  { key: "tier", header: "Tier", value: (d) => d.tier, filter: true, hidden: true },
  { key: "status", header: "Status", value: (d) => d.status, filter: true, render: (d) => <Badge tone={TONE[d.status]}>{d.status}</Badge> },
  { key: "eligible", header: "Eligible", value: (d) => d.eligible ?? null, align: "right" },
  { key: "applicants", header: "Applied", value: (d) => d.applicants, align: "right" },
  { key: "takeup", header: "Take-up", value: (d) => takeUp(d), align: "right",
    render: (d) => {
      const t = takeUp(d);
      return t === null ? "—" : (
        <span title={`${d.applicants} of ${d.eligible} eligible students applied`} className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
          <span className="mini-track"><i style={{ width: `${Math.min(100, t)}%` }} /></span>{t}%
        </span>
      );
    } },
  { key: "target", header: "Target hires", value: (d) => d.target_hires, align: "right", hidden: true },
  { key: "deadline", header: "Applications close", value: (d) => d.apply_deadline ?? "", render: (d) => <span className="small">{dateTime(d.apply_deadline)}</span> },
  { key: "actions", header: "", sortable: false, pinned: true, csv: false,
    render: (d) => (["live", "closed", "open"].includes(d.status) ? <Link className="btn small" to={`/cell/drives/${d.id}/control`}>Control room</Link> : null) },
];

export function CellDrives() {
  const [creating, setCreating] = useState(false);
  const { data, isLoading, error } = useQuery({ queryKey: ["cell", "drives"], queryFn: () => api.get<Drive[]>("/api/cell/drives") });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  return (
    <>
      <PageHead title="Drives" lead="Set up drives from a JD, approve rules, schedule rounds and run the day."
        right={<Button variant="primary" onClick={() => setCreating(true)}>New drive</Button>} />
      <Card>
        {data.length === 0 ? (
          <Empty title="No drives yet">Create a drive and paste its JD; HireFlo proposes the eligibility rules.</Empty>
        ) : (
          <DataTable rows={data} columns={DRIVE_COLUMNS} rowKey={(d) => d.id} exportName="drives" pageSize={25}
            searchPlaceholder="Search company or role" initialSort={{ key: "deadline", dir: "desc" }} />
        )}
      </Card>
      {creating && <NewDrive onClose={() => setCreating(false)} />}
    </>
  );
}
