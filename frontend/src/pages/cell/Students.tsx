import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, download } from "../../api/client";
import type { ImportReport, Paged, Student } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { Badge, Button, Card, ConfirmButton, ErrorBox, Field, Loading, Modal, PageHead, useAction } from "../../components/ui";
import { plural } from "../../lib/format";

const BRANCHES = ["", "CSE", "ECE", "EEE", "ME", "CE", "CHE", "PE"];

function EditStudent({ student, onClose }: { student: Student; onClose: () => void }) {
  const [form, setForm] = useState({ cgpa: student.cgpa, backlogs: student.backlogs, branch: student.branch, verified: student.verified });
  const save = useAction(() => api.patch(`/api/cell/students/${student.id}`, form), {
    success: "Saved. Eligibility recomputed for upcoming drives.", invalidate: [["cell"]], onDone: onClose,
  });
  const archive = useAction(() => api.del(`/api/cell/students/${student.id}`), { success: "Student archived.", invalidate: [["cell"]], onDone: onClose });
  return (
    <Modal title={`${student.name} (${student.roll_no})`} onClose={onClose}>
      <div className="stack">
        <div className="form-grid">
          <Field label="CGPA"><input type="number" step={0.01} min={0} max={10} value={form.cgpa} onChange={(e) => setForm({ ...form, cgpa: Number(e.target.value) })} /></Field>
          <Field label="Active backlogs"><input type="number" min={0} value={form.backlogs} onChange={(e) => setForm({ ...form, backlogs: Number(e.target.value) })} /></Field>
          <Field label="Branch">
            <select value={form.branch} onChange={(e) => setForm({ ...form, branch: e.target.value })}>
              {BRANCHES.filter(Boolean).map((b) => <option key={b}>{b}</option>)}
            </select>
          </Field>
        </div>
        <label className="row small"><input type="checkbox" checked={form.verified} onChange={(e) => setForm({ ...form, verified: e.target.checked })} /> Documents verified</label>
        <div className="row">
          <Button variant="primary" busy={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button>
          <ConfirmButton title="Archive this student?" confirmLabel="Archive"
            message="They are left out of eligibility, forecasts and new drives. Past applications and offers are kept."
            onConfirm={() => archive.mutate(undefined)}>Archive</ConfirmButton>
        </div>
      </div>
    </Modal>
  );
}

function ImportPanel() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const run = useAction(
    (dry: boolean) => {
      const form = new FormData();
      form.append("file", file as File);
      return api.upload<ImportReport>(`/api/cell/students/import?dry_run=${dry}`, form);
    },
    {
      success: (r) => (r.dry_run ? "Checked the sheet. Nothing saved yet." : `Imported: ${r.created} new, ${r.updated} updated.`),
      invalidate: [["cell"]],
      onDone: setReport,
    },
  );
  return (
    <Card title="Import the master sheet" subtitle="Upload the CSV the cell already keeps. Column names are matched loosely; bad rows are reported, not guessed."
      action={<Button size="small" variant="ghost" onClick={() => void download("/api/cell/students/template.csv", "master_sheet_template.csv")}>Template</Button>}>
      <div className="row">
        <input ref={fileRef} type="file" accept=".csv,text/csv" hidden onChange={(e) => { setFile(e.target.files?.[0] ?? null); setReport(null); }} />
        <Button onClick={() => fileRef.current?.click()}>{file ? file.name : "Choose CSV"}</Button>
        <Button disabled={!file} busy={run.isPending} onClick={() => run.mutate(true)}>Check sheet</Button>
        <Button variant="primary" disabled={!file} busy={run.isPending} onClick={() => run.mutate(false)}>Import</Button>
      </div>
      {report && (
        <div className="stack" style={{ marginTop: 14 }}>
          <div className="row">
            <Badge tone="ok">{report.created} new</Badge>
            <Badge tone="info">{report.updated} updated</Badge>
            <Badge>{report.unchanged} unchanged</Badge>
            {report.errors.length > 0 && <Badge tone="bad">{plural(report.errors.length, "row")} skipped</Badge>}
          </div>
          {report.errors.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Row</th><th>Roll no</th><th>Problem</th></tr></thead>
                <tbody>{report.errors.map((e) => <tr key={e.row}><td>{e.row}</td><td>{e.roll_no ?? "—"}</td><td>{e.problems.join("; ")}</td></tr>)}</tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

const STUDENT_COLUMNS: Column<Student>[] = [
  { key: "roll_no", header: "Roll no", value: (s) => s.roll_no, pinned: true, render: (s) => <span className="num">{s.roll_no}</span> },
  { key: "name", header: "Name", value: (s) => s.name, pinned: true,
    render: (s) => <><strong>{s.name}</strong><div className="small faint">{s.email}</div></> },
  { key: "email", header: "E-mail", value: (s) => s.email, hidden: true },
  { key: "programme", header: "Programme", value: (s) => s.programme, filter: true },
  { key: "branch", header: "Branch", value: (s) => s.branch, filter: true },
  { key: "cgpa", header: "CGPA", value: (s) => s.cgpa, align: "right", render: (s) => s.cgpa.toFixed(2) },
  { key: "backlogs", header: "Backlogs", value: (s) => s.backlogs, align: "right" },
  { key: "grad_year", header: "Batch", value: (s) => s.grad_year, filter: true, align: "right" },
  { key: "skills", header: "Skills", value: (s) => s.skills.join(", "), hidden: true, sortable: false },
  { key: "verified", header: "Verified", value: (s) => s.verified, filter: true,
    render: (s) => (s.verified ? <Badge tone="ok">Yes</Badge> : <Badge tone="warn">No</Badge>) },
];

export function CellStudents() {
  const [params] = useSearchParams();
  const [editing, setEditing] = useState<Student | null>(null);
  const list = useQuery({
    queryKey: ["cell", "students", "all"],
    queryFn: () => api.get<Paged<Student>>("/api/cell/students?per_page=2000"),
  });
  const rows = list.data?.items ?? [];
  const avg = rows.length ? rows.reduce((a, s) => a + s.cgpa, 0) / rows.length : 0;
  return (
    <>
      <PageHead title="Students" lead="Profiles come from the master sheet. Students never fill a signup form." />
      <ImportPanel />
      <Card title={list.data ? plural(list.data.total, "student") : "Students"}
        subtitle={rows.length ? `Average CGPA ${avg.toFixed(2)} · ${rows.filter((s) => s.backlogs > 0).length} with backlogs · ${rows.filter((s) => !s.verified).length} not verified. Click a row to edit.` : undefined}>
        {list.isLoading ? <Loading /> : list.error ? <ErrorBox error={list.error} /> : (
          <DataTable rows={rows} columns={STUDENT_COLUMNS} rowKey={(s) => s.id} onRowClick={setEditing}
            searchPlaceholder="Search name, roll no, e-mail or skill" exportName="students" pageSize={25}
            initialSort={{ key: "roll_no", dir: "asc" }} initialSearch={params.get("q") ?? ""} externalFilter={params.get("branch") ? { branch: params.get("branch")! } : undefined}
            empty="No students yet. Import the master sheet above." />
        )}
      </Card>
      {editing && <EditStudent student={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
