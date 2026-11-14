import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Offer, OfferWhatIf, StudentDashboard } from "../../api/types";
import { Badge, Button, Card, Empty, ErrorBox, Loading, Modal, PageHead, useAction } from "../../components/ui";
import { dateTime, lpa, relative } from "../../lib/format";

const pctOf = (p: number) => `${Math.round(p * 100)}%`;

/** Accept vs decline, side by side: which drives you keep, which you lose, and rough odds of doing better. */
function OfferSimulator({ offerId }: { offerId: number }) {
  const [showAll, setShowAll] = useState(false);
  const q = useQuery({ queryKey: ["me", "what-if", offerId], queryFn: () => api.get<OfferWhatIf>(`/api/me/offers/${offerId}/what-if`) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const w = q.data;
  const s = w.summary;
  const rows = showAll ? w.drives : w.drives.slice(0, 8);
  return (
    <div className="stack" style={{ gap: 8 }}>
      <h3>What this decision does to your season</h3>
      <div className={`callout ${w.offer.ends_season ? "warn" : "signal"}`}>{w.verdict}</div>
      <div className="sim-split">
        <div className="sim-side accept">
          <h3>If you accept</h3>
          <div className="v">{lpa(w.offer.ctc_lpa)}</div>
          <div className="small muted">guaranteed</div>
          {w.offer.ends_season ? (
            <p className="small" style={{ marginTop: 8 }}>Season ends. No further drives.</p>
          ) : (
            <p className="small" style={{ marginTop: 8 }}>
              Keep <strong>{s.keep}</strong> drive(s), lose <strong>{s.lose}</strong>.{" "}
              {s.accept_upgrade_targets.length
                ? <>About <strong>{pctOf(s.accept_upgrade_odds)}</strong> chance of an upgrade using your {w.chances.left_after_accept} chance(s) on {s.accept_upgrade_targets.join(", ")}.</>
                : <>No {w.threshold_lpa}+ LPA drive you'd still qualify for is open right now.</>}
            </p>
          )}
        </div>
        <div className="sim-side decline">
          <h3>If you decline</h3>
          <div className="v">{s.decline_better_targets.length ? pctOf(s.decline_better_odds) : "—"}</div>
          <div className="small muted">rough chance of a better offer</div>
          <p className="small" style={{ marginTop: 8 }}>
            {s.decline_better_targets.length
              ? <>From drives paying more that you're eligible for today: {s.decline_better_targets.join(", ")}. You'd give up a sure {lpa(w.offer.ctc_lpa)}.</>
              : "No open drive pays more than this offer and accepts you today."}
          </p>
        </div>
      </div>
      {w.drives.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Drive</th><th>CTC</th><th>Today</th><th>After accepting</th><th>Rough odds</th></tr></thead>
            <tbody>
              {rows.map((d) => {
                const lost = d.eligible_now && !d.eligible_after;
                return (
                  <tr key={d.drive_id} className={lost ? "lost" : ""} title={d.reason ?? undefined}>
                    <td><Link to={`/student/drives/${d.drive_id}`}>{d.company}</Link> <span className="small faint">{d.tier}</span></td>
                    <td className="num">{d.ctc_lpa}</td>
                    <td>{d.eligible_now ? <Badge tone="ok">Eligible</Badge> : <Badge>No</Badge>}</td>
                    <td>
                      {d.eligible_after ? <Badge tone="ok">{d.uses_chance ? "Uses a chance" : "Still eligible"}</Badge> : <Badge tone="bad">Lost</Badge>}
                      {lost && d.reason && <div className="small muted">{d.reason}</div>}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                        <span className="odds-bar"><i style={{ width: pctOf(d.odds) }} /></span>
                        <span className="num small">{pctOf(d.odds)}</span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {w.drives.length > 8 && <Button size="small" variant="ghost" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${w.drives.length}`}</Button>}
      <p className="small faint">
        {w.basis.method} Your factor: {w.basis.personal_factor}x the batch.
      </p>
    </div>
  );
}

export function StudentOffers() {
  const [confirm, setConfirm] = useState<Offer | null>(null);
  const offers = useQuery({ queryKey: ["me", "offers"], queryFn: () => api.get<Offer[]>("/api/me/offers") });
  const dash = useQuery({ queryKey: ["me", "dashboard"], queryFn: () => api.get<StudentDashboard>("/api/me/dashboard") });
  const decide = useAction(
    ({ id, accept }: { id: number; accept: boolean }) => api.post<Offer>(`/api/me/offers/${id}/${accept ? "accept" : "decline"}`),
    { success: (o) => (o.status === "accepted" ? "Offer accepted. Congratulations!" : "Offer declined."), invalidate: [["me"]], onDone: () => setConfirm(null) },
  );
  if (offers.isLoading) return <Loading />;
  if (offers.error || !offers.data) return <ErrorBox error={offers.error} />;
  const impact = (id: number) => dash.data?.opportunity.lost_if_accept.find((l) => l.offer_id === id);

  return (
    <>
      <PageHead title="Offers" lead="See exactly what accepting changes before you decide." />
      {offers.data.length === 0 ? (
        <Card><Empty title="No offers yet">When a company releases an offer, it appears here with its deadline.</Empty></Card>
      ) : (
        offers.data.map((o) => {
                  return (
            <Card key={o.id} title={`${o.company}: ${o.role}`} subtitle={`${lpa(o.ctc_lpa)}, ${o.tier} tier`}
              action={<Badge tone={o.status === "accepted" ? "ok" : o.status === "pending" ? "warn" : undefined}>{o.status}</Badge>}>
              {o.status === "pending" && (
                <div className="stack">
                  <p>Respond by <strong>{dateTime(o.deadline)}</strong> ({relative(o.deadline)}).</p>
                  <OfferSimulator offerId={o.id} />
                  <div className="row">
                    <Button variant="primary" onClick={() => setConfirm(o)}>Accept offer</Button>
                    <Button busy={decide.isPending} onClick={() => decide.mutate({ id: o.id, accept: false })}>Decline</Button>
                  </div>
                </div>
              )}
            </Card>
          );
        })
      )}
      {confirm && (
        <Modal title={`Accept ${confirm.company}?`} onClose={() => setConfirm(null)}>
          <div className="stack">
            <p>Accepting is final. Institute policy will update your eligibility for every upcoming drive straight away.</p>
            {impact(confirm.id)?.lost.length ? (
              <div className="callout warn">You'll lose access to {impact(confirm.id)?.lost.join(", ")}.</div>
            ) : null}
            <div className="row">
              <Button variant="primary" busy={decide.isPending} onClick={() => decide.mutate({ id: confirm.id, accept: true })}>
                Accept offer
              </Button>
              <Button variant="ghost" onClick={() => setConfirm(null)}>Not yet</Button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
