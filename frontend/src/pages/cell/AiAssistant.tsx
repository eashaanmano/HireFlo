import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { LlmStatus, LlmTest } from "../../api/types";
import { AssistantChat, CELL_SUGGESTIONS } from "../../components/Assistant";
import { Badge, Button, Card, Loading, PageHead, useAction } from "../../components/ui";

const TASKS: Record<string, string> = {
  chat: "Assistant chat",
  extract: "JD rule extraction",
  rewrite: "Plain-language answers",
};

/** The placement cell's assistant, plus which AI providers are connected and a one-click connection test. */
export function CellAi() {
  const status = useQuery({ queryKey: ["cell", "llm"], queryFn: () => api.get<LlmStatus>("/api/cell/llm/status") });
  const test = useAction(() => api.post<LlmTest[]>("/api/cell/llm/test"), {});
  const s = status.data;
  return (
    <>
      <PageHead title="AI assistant" lead="Ask about the season in plain language. Only totals are shared with the AI, never individual students." />
      <div className="grid grid-2" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)", alignItems: "start" }}>
        <Card className="chat-page">
          <AssistantChat endpoint="/api/cell/assistant" storageKey="hireflo.chat.cell" suggestions={CELL_SUGGESTIONS}
            intro="Ask about placements, branches, drives, deadlines, the forecast or the inbox." />
        </Card>
        <div className="stack">
          <Card title="Connected providers" subtitle="Set in backend/.env; see docs/NEON_AZURE_GROQ.md."
            action={<Button size="small" busy={test.isPending} disabled={!s?.configured.length} onClick={() => test.mutate(undefined)}>Test</Button>}>
            {status.isLoading ? <Loading /> : !s?.configured.length ? (
              <p className="muted">No AI provider is configured. The assistant still answers eligibility questions from the policy engine and gives a season summary.</p>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {s.configured.map((p) => {
                  const r = test.data?.find((t) => t.provider.startsWith(`${p.name}:`));
                  return (
                    <div key={p.name} className="row between">
                      <span><strong>{p.name === "azure" ? "Azure OpenAI" : p.name === "groq" ? "Groq" : "Generic"}</strong> <span className="small faint">{p.model}</span></span>
                      {r ? (r.ok ? <Badge tone="ok">OK · {r.ms} ms</Badge> : <Badge tone="bad" >failed</Badge>) : <Badge>configured</Badge>}
                    </div>
                  );
                })}
                {test.data?.filter((t) => !t.ok).map((t) => (
                  <div key={t.provider} className="callout small"><strong>{t.provider}:</strong> {t.error}</div>
                ))}
              </div>
            )}
          </Card>
          {s && s.configured.length > 0 && (
            <Card title="Who does what" subtitle="First choice, then fall-backs if it fails.">
              <table>
                <tbody>
                  {Object.entries(s.routing).map(([task, chain]) => (
                    <tr key={task}><td>{TASKS[task] ?? task}</td><td className="small">{chain.join(" → ") || "policy engine only"}</td></tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
