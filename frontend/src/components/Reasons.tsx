import type { Reason } from "../api/types";

export function Reasons({ reasons, onlyFailed }: { reasons: Reason[]; onlyFailed?: boolean }) {
  const list = onlyFailed ? reasons.filter((r) => !r.ok) : reasons;
  if (!list.length) return <p className="muted small">No eligibility rules apply to this drive.</p>;
  return (
    <div>
      {list.map((r, i) => (
        <div key={`${r.code}-${i}`} className={`reason ${r.ok ? "ok" : "no"}`}>
          <span className="mark" aria-hidden>
            {r.ok ? "✓" : "✕"}
          </span>
          <div>
            <div>{r.message}</div>
            {r.code.startsWith("P") && <code>Policy {r.code}</code>}
          </div>
        </div>
      ))}
    </div>
  );
}
