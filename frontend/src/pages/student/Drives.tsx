import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { StudentDriveEntry } from "../../api/types";
import { Badge, Card, Empty, ErrorBox, Loading, PageHead, Tabs } from "../../components/ui";
import { dateTime, lpa, relative } from "../../lib/format";

type Filter = "eligible" | "applied" | "all";

export function StudentDrives() {
  const [filter, setFilter] = useState<Filter>("eligible");
  const { data, isLoading, error } = useQuery({
    queryKey: ["me", "drives"],
    queryFn: () => api.get<StudentDriveEntry[]>("/api/me/drives"),
  });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const list = data.filter((d) =>
    filter === "all" ? true : filter === "applied" ? d.application !== null : d.eligible && d.drive.status === "open",
  );
  return (
    <>
      <PageHead title="Drives" lead="Every drive shows exactly why you are or aren't eligible." />
      <Tabs<Filter>
        value={filter}
        onChange={setFilter}
        tabs={[
          { id: "eligible", label: "Open to me" },
          { id: "applied", label: "Applied" },
          { id: "all", label: "All drives" },
        ]}
      />
      <Card>
        {list.length === 0 ? (
          <Empty title="Nothing here yet">Drives you're eligible for appear as soon as the placement cell opens them.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Company and role</th>
                  <th>CTC</th>
                  <th>Status</th>
                  <th>You</th>
                  <th>Deadline</th>
                </tr>
              </thead>
              <tbody>
                {list.map(({ drive: d, eligible, reasons, application }) => (
                  <tr key={d.id}>
                    <td>
                      <Link to={`/student/drives/${d.id}`}>
                        <strong>{d.company}</strong>
                      </Link>
                      <div className="small muted">{d.role}</div>
                    </td>
                    <td className="num">
                      {lpa(d.ctc_lpa)}
                      <div className="small faint">{d.tier}{d.sector === "PSU" ? ", PSU" : ""}</div>
                    </td>
                    <td><Badge>{d.status}</Badge></td>
                    <td>
                      {application ? (
                        <Badge tone="info">{application.stage}</Badge>
                      ) : eligible ? (
                        <Badge tone="ok">Eligible</Badge>
                      ) : (
                        <span className="small" style={{ color: "var(--red)" }}>
                          {reasons.find((r) => !r.ok)?.message ?? "Not eligible"}
                        </span>
                      )}
                    </td>
                    <td className="small">
                      {dateTime(d.apply_deadline)}
                      <div className="faint">{d.status === "open" ? relative(d.apply_deadline) : ""}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
