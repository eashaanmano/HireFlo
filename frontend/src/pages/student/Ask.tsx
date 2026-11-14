import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { PolicyRule } from "../../api/types";
import { AssistantChat, STUDENT_SUGGESTIONS } from "../../components/Assistant";
import { Card, PageHead } from "../../components/ui";

/** Full-page HireFlo Assistant for students, with the policy in force alongside for reference. */
export function StudentAsk() {
  const policy = useQuery({ queryKey: ["me", "policy"], queryFn: () => api.get<PolicyRule[]>("/api/me/policy"), retry: false });
  return (
    <>
      <PageHead title="HireFlo Assistant"
        lead="Ask about your drives, eligibility, interviews and offers. Eligibility answers come from the same policy engine that decides it." />
      <div className="grid grid-2" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)", alignItems: "start" }}>
        <Card className="chat-page">
          <AssistantChat endpoint="/api/me/assistant" storageKey="hireflo.chat.student" suggestions={[
            ...STUDENT_SUGGESTIONS,
            "How do dream and super dream tiers work?",
          ]} intro="Ask anything about your placement season. I only use your own HireFlo data and the placement policy." />
        </Card>
        <Card title="Policy in force" subtitle="The rules the assistant cites as P0, P1, …">
          {policy.data?.length ? (
            <div className="stack" style={{ gap: 8 }}>
              {policy.data.map((r) => (
                <div key={r.code} className="callout small"><strong>{r.code} {r.title}.</strong> {r.text}</div>
              ))}
            </div>
          ) : <p className="muted small">Ask "what are the placement rules?" to see them.</p>}
        </Card>
      </div>
    </>
  );
}
