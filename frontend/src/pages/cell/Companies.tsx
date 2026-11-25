import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Company, JnfRow } from "../../api/types";
import { DataTable } from "../../components/DataTable";
import { Badge, Button, Card, Empty, ErrorBox, Field, Loading, Modal, PageHead, useAction } from "../../components/ui";
import { useCopyLink } from "../../lib/clipboard";
import { relative } from "../../lib/format";
import { JNF_STATUS } from "../../lib/jnf";

type Draft = Omit<Company, "id" | "archived" | "active_drives" | "live_drives">;
const BLANK: Draft = { name: "", sector: "", contact_name: "", contact_email: "", notes: "" };

function clean(d: Draft | Invite) {
  return Object.fromEntries(Object.entries(d).map(([k, v]) => [k, v === "" ? null : v]));
}

type Invite = { company_name: string; contact_name: string; contact_email: string };
type Sent = { id: number; url: string; emailed: boolean; company: string };

/** Companies fill their job notification form through a link; it lands in the inbox for review. */
function JobNotifications({ companies }: { companies: Company[] }) {
  const copy = useCopyLink();
  const [inviting, setInviting] = useState<Invite | null>(null);
  const [sent, setSent] = useState<Sent | null>(null);
  const forms = useQuery({ queryKey: ["cell", "jnf", "list"], queryFn: () => api.get<JnfRow[]>("/api/cell/jnf") });
  const invite = useAction((d: Invite) => api.post<Omit<Sent, "company">>("/api/cell/jnf", clean(d)), {
    invalidate: [["cell", "jnf"]],
    onDone: (r) => { setSent({ ...r, company: inviting?.company_name ?? "" }); setInviting(null); },
  });
  const set = (k: keyof Invite, v: string) => setInviting((e) => (e ? { ...e, [k]: v } : e));
  const pick = (name: string) => {
    const c = companies.find((x) => x.name.toLowerCase() === name.trim().toLowerCase());
    setInviting((e) => (e ? { ...e, company_name: name, contact_name: e.contact_name || c?.contact_name || "", contact_email: e.contact_email || c?.contact_email || "" } : e));
  };
  return (
    <Card title="Job notification forms" subtitle="Send a company its form link. What they fill in comes to your inbox, and approving it creates the drive."
      action={<Button onClick={() => setInviting({ company_name: "", contact_name: "", contact_email: "" })}>Send job notification form</Button>}>
      {forms.isLoading ? <Loading /> : forms.error || !forms.data ? <ErrorBox error={forms.error} /> : forms.data.length === 0 ? (
        <Empty title="No forms sent yet">A company gets a secure link, sees how many students meet its criteria as it fills the form, and needs no account.</Empty>
      ) : (
        <DataTable rows={forms.data} rowKey={(n) => n.id} exportName="job_notifications" pageSize={10}
          searchPlaceholder="Search company or role"
          columns={[
            { key: "company", header: "Company", value: (n) => n.company_name, pinned: true,
              render: (n) => <><strong>{n.company_name}</strong><div className="small faint">{n.contact_email ?? n.contact_name ?? ""}</div></> },
            { key: "role", header: "Role", value: (n) => n.role ?? "", render: (n) => n.role ?? <span className="faint">not sent yet</span> },
            { key: "status", header: "Status", value: (n) => JNF_STATUS[n.status].label, filter: true,
              render: (n) => <Badge tone={JNF_STATUS[n.status].tone}>{JNF_STATUS[n.status].label}</Badge> },
            { key: "when", header: "Last change", value: (n) => n.decided_at ?? n.submitted_at ?? n.created_at ?? "",
              render: (n) => relative(n.decided_at ?? n.submitted_at ?? n.created_at) },
            { key: "open", header: "", sortable: false, csv: false, pinned: true,
              render: (n) => <Link className="btn small" to={`/cell/jnf/${n.id}`}>{n.status === "submitted" ? "Review" : "Open"}</Link> },
          ]} />
      )}
      {inviting && (
        <Modal title="Send a job notification form" onClose={() => setInviting(null)}>
          <div className="stack">
            <div className="form-grid">
              <Field label="Company">
                <input list="jnf-companies" value={inviting.company_name} onChange={(e) => pick(e.target.value)} autoFocus />
                <datalist id="jnf-companies">{companies.filter((c) => !c.archived).map((c) => <option key={c.id} value={c.name} />)}</datalist>
              </Field>
              <Field label="HR contact name"><input value={inviting.contact_name} onChange={(e) => set("contact_name", e.target.value)} /></Field>
              <Field label="HR e-mail"><input type="email" value={inviting.contact_email} onChange={(e) => set("contact_email", e.target.value)} /></Field>
            </div>
            <p className="small muted">The link works for 30 days. It is e-mailed to the contact where e-mail is set up, and you can always copy it.</p>
            <Button variant="primary" busy={invite.isPending} disabled={inviting.company_name.trim().length < 2} onClick={() => invite.mutate(inviting)}>Create the link</Button>
          </div>
        </Modal>
      )}
      {sent && (
        <Modal title={`Form link for ${sent.company}`} onClose={() => setSent(null)}>
          <div className="stack">
            <p>{sent.emailed ? "We e-mailed this link to the contact. You can also send it yourself:" : "Send this link to the company's HR contact:"}</p>
            <input readOnly value={sent.url} onFocus={(e) => e.currentTarget.select()} aria-label="Form link" />
            <div className="row">
              <Button variant="primary" onClick={() => copy(sent.url, "Form link")}>Copy link</Button>
              <Link className="btn" to={`/cell/jnf/${sent.id}`}>Open the form's page</Link>
            </div>
          </div>
        </Modal>
      )}
    </Card>
  );
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
      <div style={{ height: 16 }} />
      <JobNotifications companies={list.data} />
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
