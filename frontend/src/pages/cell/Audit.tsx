import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { AuditEvent } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { Card, ErrorBox, Loading, PageHead } from "../../components/ui";
import { dateTime } from "../../lib/format";

const details = (e: AuditEvent) =>
  [e.entity ? `${e.entity} #${e.entity_id}` : "", e.drive_id ? `drive #${e.drive_id}` : "",
    Object.keys(e.payload).length ? JSON.stringify(e.payload) : ""].filter(Boolean).join(" · ");

const COLUMNS: Column<AuditEvent>[] = [
  { key: "ts", header: "When", value: (e) => e.ts, render: (e) => <span className="small num" style={{ whiteSpace: "nowrap" }}>{dateTime(e.ts)}</span> },
  { key: "area", header: "Area", value: (e) => e.type.split(".")[0], filter: true },
  { key: "type", header: "Event", value: (e) => e.type, render: (e) => <code>{e.type}</code> },
  { key: "actor", header: "Actor", value: (e) => e.actor, filter: true, render: (e) => <span className="small">{e.actor}</span> },
  { key: "details", header: "Details", value: details, sortable: false, render: (e) => <span className="small faint">{details(e)}</span> },
];

export function CellAudit() {
  const q = useQuery({ queryKey: ["cell", "audit", "recent"], queryFn: () => api.get<AuditEvent[]>("/api/cell/audit?limit=500") });
  return (
    <>
      <PageHead title="Audit log" lead="Every decision, override and change: who, when and what. The latest 500 events." />
      <Card>
        {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : (
          <DataTable rows={q.data ?? []} columns={COLUMNS} rowKey={(e) => e.id} exportName="audit_log" pageSize={50}
            initialSort={{ key: "ts", dir: "desc" }} searchPlaceholder="Search events, actors or details" />
        )}
      </Card>
    </>
  );
}
