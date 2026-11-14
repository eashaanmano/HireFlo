import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Loading } from "../components/ui";
import { useAuth } from "../lib/auth";

interface CheckinResult {
  slot_id: number;
  already: boolean;
  panel: string;
  room: string;
}

/** Opened by scanning the venue QR with the phone camera. Checks the student in to their next slot. */
export function CheckIn() {
  const { me, loading } = useAuth();
  const [params] = useSearchParams();
  const token = params.get("t") ?? "";
  const [result, setResult] = useState<CheckinResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sent = useRef(false);

  useEffect(() => {
    if (!me || me.role !== "student" || !token || sent.current) return;
    sent.current = true;
    api
      .post<CheckinResult>("/api/me/checkin", { token })
      .then(setResult)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Check-in failed."));
  }, [me, token]);

  if (loading) return <div className="main"><Loading /></div>;
  if (!me) return <Navigate to={`/login?next=${encodeURIComponent(`/checkin?t=${token}`)}`} replace />;

  return (
    <div className="login-main" style={{ minHeight: "100%" }}>
      <div className="board" style={{ width: "min(420px, 100%)", textAlign: "center", padding: 28 }}>
        {me.role !== "student" && <p>Check-in is for students. Open the control room to manage arrivals.</p>}
        {!result && !error && me.role === "student" && <p className="label">Checking you in…</p>}
        {result && (
          <>
            <p className="label">{result.already ? "You were already checked in" : "You're checked in"}</p>
            <p className="big" style={{ margin: "12px 0" }}>{result.panel}</p>
            <p style={{ fontSize: 18 }}>{result.room}</p>
            <p className="label" style={{ marginTop: 16 }}>Your live queue position is on your home screen.</p>
          </>
        )}
        {error && <p style={{ color: "#ff9a8a" }}>{error}</p>}
        <Link to="/student" className="btn primary" style={{ marginTop: 20 }}>
          Open my home screen
        </Link>
      </div>
    </div>
  );
}
