import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { PolicyConfig, PolicyResponse } from "../../api/types";
import { useAuth } from "../../lib/auth";
import { Badge, Button, Card, ErrorBox, Field, Loading, PageHead, useAction } from "../../components/ui";
import { dateTime } from "../../lib/format";

export function CellPolicy() {
  const { me } = useAuth();
  const q = useQuery({ queryKey: ["cell", "policy"], queryFn: () => api.get<PolicyResponse>("/api/cell/policy") });
  const [cfg, setCfg] = useState<PolicyConfig | null>(null);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (q.data) setCfg(q.data.active.config);
  }, [q.data]);
  const save = useAction(() => api.put("/api/cell/policy", { config: cfg, note: note || null }), {
    success: "New policy version saved. Eligibility recomputed for every upcoming drive.",
    invalidate: [["cell"]],
    onDone: () => setNote(""),
  });
  if (q.isLoading || !cfg) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const isAdmin = me?.role === "admin";
  const setTier = (i: number, k: "name" | "min_ctc_lpa", v: string) =>
    setCfg({ ...cfg, tiers: cfg.tiers.map((t, j) => (j === i ? { ...t, [k]: k === "name" ? v : Number(v) } : t)) });

  return (
    <>
      <PageHead title="Placement policy" lead="Encoded once, applied automatically, cited in every explanation."
        right={<Badge tone="info">Version {q.data.active.version}</Badge>} />
      <div className="grid grid-2">
        <Card title="Rules in force">
          {q.data.rules.map((r) => (
            <div key={r.code} className="reason ok">
              <span className="mark">{r.code}</span>
              <div><strong>{r.title}.</strong> {r.text}</div>
            </div>
          ))}
        </Card>
        <Card title="Edit policy" subtitle={isAdmin ? "Saving creates a new version; the old one stays in history." : "Only admins can change the policy."}>
          <fieldset disabled={!isAdmin} style={{ border: 0, padding: 0, margin: 0 }} className="stack">
            <div className="stack" style={{ gap: 8 }}>
              <strong className="small">Tiers</strong>
              {cfg.tiers.map((t, i) => (
                <div className="row" key={i}>
                  <input value={t.name} onChange={(e) => setTier(i, "name", e.target.value)} style={{ maxWidth: 160 }} aria-label="Tier name" />
                  <span className="small muted">from</span>
                  <input type="number" min={0} step={0.5} value={t.min_ctc_lpa} onChange={(e) => setTier(i, "min_ctc_lpa", e.target.value)} style={{ maxWidth: 90 }} aria-label="Minimum CTC" />
                  <span className="small muted">LPA</span>
                </div>
              ))}
            </div>
            <label className="row small"><input type="checkbox" checked={cfg.upgrade_only} onChange={(e) => setCfg({ ...cfg, upgrade_only: e.target.checked })} /> Placed students may only sit for higher tiers</label>
            <div className="form-grid">
              <Field label="Offer that ends the season (LPA)"><input type="number" min={0} step={0.5} value={cfg.freeze_at_ctc_lpa} onChange={(e) => setCfg({ ...cfg, freeze_at_ctc_lpa: Number(e.target.value) })} /></Field>
              <Field label="Chances above that after a lower offer"><input type="number" min={0} max={10} value={cfg.upgrade_attempts} onChange={(e) => setCfg({ ...cfg, upgrade_attempts: Number(e.target.value) })} /></Field>
              <Field label="Sectors whose offer ends the season"><input value={cfg.freeze_sectors.join(", ")} onChange={(e) => setCfg({ ...cfg, freeze_sectors: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} placeholder="PSU" /></Field>
            </div>
            <div className="form-grid">
              <Field label="Max accepted offers"><input type="number" min={1} max={10} value={cfg.max_offers} onChange={(e) => setCfg({ ...cfg, max_offers: Number(e.target.value) })} /></Field>
              <Field label="Minimum jump (LPA)"><input type="number" min={0} step={0.5} value={cfg.min_ctc_jump_lpa} onChange={(e) => setCfg({ ...cfg, min_ctc_jump_lpa: Number(e.target.value) })} /></Field>
              <Field label="Offer deadline (hours)"><input type="number" min={1} value={cfg.offer_deadline_hours} onChange={(e) => setCfg({ ...cfg, offer_deadline_hours: Number(e.target.value) })} /></Field>
            </div>
            <Field label="What changed (kept in history)"><input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
            <Button variant="primary" busy={save.isPending} onClick={() => save.mutate(undefined)}>Save new version</Button>
          </fieldset>
        </Card>
        <Card title="History" className="span-2">
          <table>
            <thead><tr><th>Version</th><th>Note</th><th>By</th><th>When</th></tr></thead>
            <tbody>
              {q.data.versions.map((v) => (
                <tr key={v.id}>
                  <td>{v.version} {v.active && <Badge tone="ok">active</Badge>}</td>
                  <td>{v.note ?? "—"}</td>
                  <td className="small">{v.created_by}</td>
                  <td className="small">{dateTime(v.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}
