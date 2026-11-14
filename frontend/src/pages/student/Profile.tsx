import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, download } from "../../api/client";
import type { Notification, Resume, Student } from "../../api/types";
import { Badge, Button, Card, ConfirmButton, ErrorBox, Field, Loading, PageHead, useAction } from "../../components/ui";
import { dateTime } from "../../lib/format";

export function StudentProfile() {
  const profile = useQuery({ queryKey: ["me", "profile"], queryFn: () => api.get<Student>("/api/me/profile") });
  const resumes = useQuery({ queryKey: ["me", "resumes"], queryFn: () => api.get<Resume[]>("/api/me/resumes") });
  const notes = useQuery({ queryKey: ["me", "notifications"], queryFn: () => api.get<Notification[]>("/api/me/notifications") });
  const [skills, setSkills] = useState("");
  const [phone, setPhone] = useState("");
  const [track, setTrack] = useState("Software");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (profile.data) {
      setSkills(profile.data.skills.join(", "));
      setPhone(profile.data.phone ?? "");
    }
  }, [profile.data]);

  const save = useAction(
    () => api.patch<Student>("/api/me/profile", { phone: phone || null, skills: skills.split(",").map((s) => s.trim()).filter(Boolean) }),
    { success: "Profile saved.", invalidate: [["me"]] },
  );
  const upload = useAction(
    (file: File) => {
      const form = new FormData();
      form.append("track", track);
      form.append("file", file);
      return api.upload<Resume>("/api/me/resumes", form);
    },
    { success: "Resume uploaded.", invalidate: [["me", "resumes"]] },
  );
  const remove = useAction((id: number) => api.del(`/api/me/resumes/${id}`), { success: "Resume deleted.", invalidate: [["me", "resumes"]] });
  const markRead = useAction(() => api.post("/api/me/notifications/read"), { invalidate: [["me"]] });

  if (profile.isLoading) return <Loading />;
  if (profile.error || !profile.data) return <ErrorBox error={profile.error} />;
  const s = profile.data;

  return (
    <>
      <PageHead title={s.name} lead={`${s.roll_no}, ${s.programme} ${s.branch}, graduating ${s.grad_year}`}
        right={s.verified ? <Badge tone="ok">Verified by the placement cell</Badge> : <Badge tone="warn">Awaiting verification</Badge>} />
      <div className="grid grid-2">
        <Card title="Academic record" subtitle="Imported from the placement cell's records. Ask the cell to correct anything wrong.">
          <div className="form-grid">
            <div><div className="faint small">CGPA</div><strong className="num">{s.cgpa.toFixed(2)}</strong></div>
            <div><div className="faint small">Active backlogs</div><strong>{s.backlogs}</strong></div>
            <div><div className="faint small">E-mail</div><span className="small">{s.email}</span></div>
          </div>
          <hr className="divider" />
          <div className="stack">
            <Field label="Skills (comma separated)">
              <input value={skills} onChange={(e) => setSkills(e.target.value)} />
            </Field>
            <Field label="Phone">
              <input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" />
            </Field>
            <Button variant="primary" busy={save.isPending} onClick={() => save.mutate(undefined)}>Save profile</Button>
          </div>
        </Card>

        <Card title="Resumes" subtitle="Keep a version per track (for example SDE, ML, core). PDF, up to 2 MB.">
          <div className="stack">
            {resumes.data?.map((r) => (
              <div className="row between" key={r.id}>
                <span><strong>{r.track}</strong> <span className="small muted">{r.filename}</span></span>
                <span className="row">
                  <Button size="small" onClick={() => void download(`/api/me/resumes/${r.id}`, r.filename)}>View</Button>
                  <ConfirmButton size="small" title={`Delete ${r.filename}?`} confirmLabel="Delete"
                    message="The file is deleted. Recruiters can no longer open it from applications that used it."
                    onConfirm={() => remove.mutate(r.id)}>Delete</ConfirmButton>
                </span>
              </div>
            ))}
            <div className="row">
              <select value={track} onChange={(e) => setTrack(e.target.value)} style={{ maxWidth: 160 }} aria-label="Track">
                {["Software", "ML / Data", "Core", "Analytics", "General"].map((t) => <option key={t}>{t}</option>)}
              </select>
              <input ref={fileRef} type="file" accept="application/pdf" hidden
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload.mutate(f);
                  e.target.value = "";
                }} />
              <Button busy={upload.isPending} onClick={() => fileRef.current?.click()}>Upload resume</Button>
            </div>
          </div>
        </Card>

        <Card title="Updates" className="span-2"
          action={<Button size="small" variant="ghost" onClick={() => markRead.mutate(undefined)}>Mark all read</Button>}>
          {notes.data?.length ? (
            notes.data.map((n) => (
              <div key={n.id} className="action" style={{ gridTemplateColumns: "120px 1fr" }}>
                <span className="small faint">{dateTime(n.created_at)}</span>
                <div>
                  <strong>{n.title}</strong> {!n.read && <Badge tone="info">new</Badge>}
                  <div className="small muted">{n.body}</div>
                </div>
              </div>
            ))
          ) : (
            <p className="muted">Updates about your drives appear here. Only messages meant for you are sent.</p>
          )}
        </Card>
      </div>
    </>
  );
}
