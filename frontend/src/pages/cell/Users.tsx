import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { CellUser } from "../../api/types";
import { Badge, Button, Card, ConfirmButton, ErrorBox, Field, Loading, PageHead, useAction } from "../../components/ui";

export function CellUsers() {
  const q = useQuery({ queryKey: ["cell", "users"], queryFn: () => api.get<CellUser[]>("/api/cell/users") });
  const [form, setForm] = useState({ email: "", name: "", role: "coordinator" as "coordinator" | "admin" });
  const invite = useAction(() => api.post("/api/cell/users", form), {
    success: "Teammate added. They sign in with an e-mail code.", invalidate: [["cell", "users"]],
    onDone: () => setForm({ email: "", name: "", role: "coordinator" }),
  });
  const update = useAction((p: { id: number; body: Record<string, unknown> }) => api.patch(`/api/cell/users/${p.id}`, p.body), {
    success: "Access updated.", invalidate: [["cell", "users"]],
  });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  return (
    <>
      <PageHead title="Placement cell team" lead="Coordinators run drives; admins also manage policy and team access." />
      <div className="grid grid-2">
        <Card title="Team">
          <table>
            <thead><tr><th>Name</th><th>Role</th><th /></tr></thead>
            <tbody>
              {q.data.map((u) => (
                <tr key={u.id}>
                  <td><strong>{u.name}</strong><div className="small faint">{u.email}</div></td>
                  <td><Badge tone={u.role === "admin" ? "info" : undefined}>{u.role}</Badge> {!u.active && <Badge tone="bad">disabled</Badge>}</td>
                  <td className="row">
                    <Button size="small" onClick={() => update.mutate({ id: u.id, body: { role: u.role === "admin" ? "coordinator" : "admin" } })}>
                      Make {u.role === "admin" ? "coordinator" : "admin"}
                    </Button>
                    {u.active ? (
                      <ConfirmButton size="small" title={`Disable ${u.name}?`} confirmLabel="Disable"
                        message="They are signed out and can no longer sign in. You can enable them again later."
                        onConfirm={() => update.mutate({ id: u.id, body: { active: false } })}>Disable</ConfirmButton>
                    ) : (
                      <Button size="small" onClick={() => update.mutate({ id: u.id, body: { active: true } })}>Enable</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Add a teammate">
          <div className="stack">
            <Field label="Name"><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="E-mail"><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Role">
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as "coordinator" | "admin" })}>
                <option value="coordinator">Coordinator</option>
                <option value="admin">Admin</option>
              </select>
            </Field>
            <Button variant="primary" busy={invite.isPending} disabled={!form.email || form.name.length < 2} onClick={() => invite.mutate(undefined)}>Add teammate</Button>
          </div>
        </Card>
      </div>
    </>
  );
}
