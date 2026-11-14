import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { Company } from "../../api/types";
import { DataTable } from "../../components/DataTable";
import { Badge, Button, Card, Empty, ErrorBox, Field, Loading, Modal, PageHead, useAction } from "../../components/ui";

type Draft = Omit<Company, "id" | "archived" | "active_drives" | "live_drives">;
const BLANK: Draft = { name: "", sector: "", contact_name: "", contact_email: "", notes: "" };

function clean(d: Draft) {
  return Object.fromEntries(Object.entries(d).map(([k, v]) => [k, v === "" ? null : v]));
}

export function CellCompanies() {
  const [editing, setEditing] = useState<{ id: number | null; draft: Draft } | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [confirm, setConfirm] = useState<Company | null>(null);
  const list = useQuery({
    queryKey: ["cell", "companies", showArchived ? "all" : "active"],
    queryFn: () => api.get<Company[]>(`/api/cell/companies${showArchived ? "?include_archived=true" : ""}`),
  });
  const save = useAction(
    () => (editing?.id ? api.patch(`/api/cell/companies/${editing.id}`, clean(editing.draft)) : api.post("/api/cell/companies", clean(editing?.draft ?? BLANK))),
    { success: "Company saved.", invalidate: [["cell", "companies"]], onDone: () => setEditing(null) },
  );
  const archive = useAction((id: number) => api.del(`/api/cell/companies/${id}`), {
    success: "Company archived. Its existing drives and history are kept.", invalidate: [["cell", "companies"]], onDone: () => setConfirm(null),
  });
  const restore = useAction((id: number) => api.post(`/api/cell/companies/${id}/restore`, {}), {
    success: "Company restored.", invalidate: [["cell", "companies"]],
  });
  if (list.isLoading) return <Loading />;
  if (list.error || !list.data) return <ErrorBox error={list.error} />;
  const set = (k: keyof Draft, v: string) => setEditing((e) => (e ? { ...e, draft: { ...e.draft, [k]: v } } : e));
  return (
    <>
      <PageHead title="Companies" right={
        <div className="row">
          <label className="row small" style={{ gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} style={{ width: "auto" }} />
            Show archived
          </label>
          <Button variant="primary" onClick={() => setEditing({ id: null, draft: BLANK })}>Add company</Button>
        </div>
      } />
      <Card>
        {list.data.length === 0 ? <Empty title="No companies yet" /> : (
          <DataTable rows={list.data} rowKey={(c) => c.id} exportName="companies" pageSize={25}
            initialSort={{ key: "name", dir: "asc" }} searchPlaceholder="Search company, sector or contact"
            rowClassName={(c) => (c.archived ? "archived" : undefined)}
            columns={[
              { key: "name", header: "Company", value: (c) => c.name, pinned: true,
                render: (c) => <><strong>{c.name}</strong> {c.archived && <Badge tone="warn">archived</Badge>}{c.notes && <div className="small faint">{c.notes}</div>}</> },
              { key: "sector", header: "Sector", value: (c) => c.sector ?? "", filter: true },
              { key: "contact", header: "Contact", value: (c) => c.contact_name ?? "",
                render: (c) => <>{c.contact_name ?? "—"}<div className="small faint">{c.contact_email}</div></> },
              { key: "email", header: "Contact e-mail", value: (c) => c.contact_email ?? "", hidden: true },
              { key: "active", header: "Active drives", value: (c) => c.active_drives, align: "right",
                render: (c) => <>{c.active_drives}{c.live_drives > 0 && <> <Badge tone="info">{c.live_drives} live</Badge></>}</> },
              { key: "actions", header: "", sortable: false, pinned: true, csv: false,
                render: (c) => (
                  <span className="row" style={{ flexWrap: "nowrap" }}>
                    <Button size="small" onClick={() => setEditing({ id: c.id, draft: { name: c.name, sector: c.sector ?? "", contact_name: c.contact_name ?? "", contact_email: c.contact_email ?? "", notes: c.notes ?? "" } })}>Edit</Button>
                    {c.archived
                      ? <Button size="small" busy={restore.isPending} onClick={() => restore.mutate(c.id)}>Restore</Button>
                      : <Button size="small" variant="danger" onClick={() => setConfirm(c)}>Archive</Button>}
                  </span>
                ) },
            ]} />
        )}
      </Card>
      {confirm && (
        <Modal title={`Archive ${confirm.name}?`} onClose={() => setConfirm(null)}>
          <div className="stack">
            {confirm.live_drives > 0 ? (
              <p>{confirm.name} has a drive running today. Complete that drive before archiving the company.</p>
            ) : (
              <>
                <p>The company is hidden from this list and can no longer be picked for a new drive. Nothing is deleted, and you can restore it from <strong>Show archived</strong>.</p>
                {confirm.active_drives > 0 && (
                  <p><Badge tone="warn">{confirm.active_drives} active drive{confirm.active_drives > 1 ? "s" : ""}</Badge> {confirm.active_drives > 1 ? "These keep" : "This keeps"} running: students can still apply, links still work and offers stay valid.</p>
                )}
              </>
            )}
            <div className="row">
              <Button onClick={() => setConfirm(null)}>Cancel</Button>
              <Button variant="danger" busy={archive.isPending} disabled={confirm.live_drives > 0} onClick={() => archive.mutate(confirm.id)}>Archive company</Button>
            </div>
          </div>
        </Modal>
      )}
      {editing && (
        <Modal title={editing.id ? "Edit company" : "Add company"} onClose={() => setEditing(null)}>
          <div className="stack">
            <div className="form-grid">
              <Field label="Name"><input value={editing.draft.name} onChange={(e) => set("name", e.target.value)} /></Field>
              <Field label="Sector"><input value={editing.draft.sector ?? ""} onChange={(e) => set("sector", e.target.value)} /></Field>
              <Field label="Contact name"><input value={editing.draft.contact_name ?? ""} onChange={(e) => set("contact_name", e.target.value)} /></Field>
              <Field label="Contact e-mail"><input type="email" value={editing.draft.contact_email ?? ""} onChange={(e) => set("contact_email", e.target.value)} /></Field>
            </div>
            <Field label="Notes"><textarea value={editing.draft.notes ?? ""} onChange={(e) => set("notes", e.target.value)} style={{ minHeight: 70 }} /></Field>
            <Button variant="primary" busy={save.isPending} disabled={editing.draft.name.length < 2} onClick={() => save.mutate(undefined)}>Save company</Button>
          </div>
        </Modal>
      )}
    </>
  );
}
