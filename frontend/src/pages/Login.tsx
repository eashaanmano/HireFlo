import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Building2, CalendarClock, GraduationCap, ShieldCheck, Sparkles } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { DemoAccount, Me } from "../api/types";
import { Button } from "../components/ui";
import { homeFor, useAuth } from "../lib/auth";

interface OtpRequested {
  sent: boolean;
  demo_code: string | null;
  message: string;
}

// Illustrative board for the landing page (mirrors the seeded demo drives).
const BOARD: { company: string; role: string; ctc: string; status: string; tone: "live" | "open" | "soon" }[] = [
  { company: "AMD", role: "Silicon Design Engineer", ctc: "21 LPA", status: "Interviews live", tone: "live" },
  { company: "Qualcomm", role: "Embedded Software", ctc: "24 LPA", status: "Closes in 2 days", tone: "open" },
  { company: "NVIDIA", role: "System Software", ctc: "32 LPA", status: "Applications open", tone: "open" },
  { company: "Microsoft", role: "Software Engineer", ctc: "45 LPA", status: "Applications open", tone: "open" },
  { company: "IOC", role: "Graduate Engineer (PSU)", ctc: "16 LPA", status: "Applications open", tone: "open" },
  { company: "ARM", role: "Compilers", ctc: "22 LPA", status: "Opening soon", tone: "soon" },
];

const ACTORS = [
  { icon: GraduationCap, title: "Students", text: "Know exactly why you're eligible, where you're losing, and when you're next in the queue." },
  { icon: ShieldCheck, title: "Placement cell", text: "Only exceptions reach you. Schedules re-plan themselves when interviews run late." },
  { icon: Building2, title: "Recruiters", text: "No account. Preview the pool, shortlist and run panels from one secure link." },
];

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("");
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(t);
  }, []);
  return (
    <span className="landing-clock num">
      {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
    </span>
  );
}

export function Login() {
  const { me, refresh } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const demo = useQuery({
    queryKey: ["demo-accounts"],
    queryFn: () => api.get<DemoAccount[]>("/api/auth/demo-accounts"),
    retry: false,
  });

  useEffect(() => {
    if (step === "code") codeRef.current?.focus();
  }, [step]);

  if (me) return <Navigate to={params.get("next") || homeFor(me)} replace />;

  const finish = async (user: Me) => {
    await refresh();
    navigate(params.get("next") || homeFor(user), { replace: true });
  };

  const requestCode = async (target: string) => {
    setBusy("email");
    setError(null);
    try {
      const r = await api.post<OtpRequested>("/api/auth/otp/request", { email: target });
      setEmail(target);
      setStep("code");
      setHint(r.demo_code ? "Demo mode: the code is filled in for you." : r.message);
      if (r.demo_code) setCode(r.demo_code);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't send the code. Try again.");
    } finally {
      setBusy(null);
    }
  };

  const verify = async (e: FormEvent) => {
    e.preventDefault();
    setBusy("code");
    setError(null);
    try {
      await finish(await api.post<Me>("/api/auth/otp/verify", { email, code }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't sign you in.");
    } finally {
      setBusy(null);
    }
  };

  /** One click in demo mode: request the code and verify it straight away. */
  const quickSignIn = async (account: DemoAccount) => {
    setBusy(account.email);
    setError(null);
    try {
      const r = await api.post<OtpRequested>("/api/auth/otp/request", { email: account.email });
      if (!r.demo_code) {
        setEmail(account.email);
        setStep("code");
        setHint(r.message);
        return;
      }
      await finish(await api.post<Me>("/api/auth/otp/verify", { email: account.email, code: r.demo_code }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't sign in with that account.");
    } finally {
      setBusy(null);
    }
  };

  const students = demo.data?.filter((a) => a.role === "student") ?? [];
  const cell = demo.data?.filter((a) => a.role !== "student") ?? [];

  return (
    <div className="landing">
      <section className="landing-hero" aria-label="About HireFlo (DriveOps)">
        <div className="row between">
          <div className="row" style={{ gap: 12 }}>
            <img src="/icon.svg" alt="" width={36} height={36} />
            <strong style={{ fontSize: 20 }}>HireFlo <span style={{ fontWeight: 500, fontSize: 15, opacity: 0.7 }}>(DriveOps)</span></strong>
          </div>
          <span className="landing-tag">NIT Calicut placement season 2026</span>
        </div>

        <div className="landing-copy">
          <h1 className="landing-title">Run placement drives, not spreadsheets.</h1>
          <p className="landing-lead">
            One live system for students, the placement cell and recruiters. Eligibility explained, interviews
            scheduled without clashes, and plans that fix themselves when the day runs late.
          </p>
        </div>

        <div className="landing-board" aria-label="Today's drives">
          <div className="landing-board-head">
            <span><CalendarClock size={16} aria-hidden /> Today's drives</span>
            <Clock />
          </div>
          {BOARD.map((b, i) => (
            <div className="landing-row" key={b.company} style={{ animationDelay: `${i * 70}ms` }}>
              <span className="co">{b.company}</span>
              <span className="role">{b.role}</span>
              <span className="ctc num">{b.ctc}</span>
              <span className={`st ${b.tone}`}>{b.status}</span>
            </div>
          ))}
          <p className="landing-note">CTCs are illustrative demo values.</p>
        </div>

        <div className="landing-actors">
          {ACTORS.map(({ icon: Icon, title, text }) => (
            <div className="landing-actor" key={title}>
              <Icon size={20} aria-hidden />
              <div>
                <strong>{title}</strong>
                <p>{text}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-side">
        <div className="signin">
          <div className="steps" aria-label="Sign-in steps">
            <span className={step === "email" ? "on" : "done"}><b>1</b> E-mail</span>
            <span className={step === "code" ? "on" : ""}><b>2</b> Code</span>
          </div>
          <h2 className="signin-title">{step === "email" ? "Sign in" : "Check your code"}</h2>
          <p className="muted" style={{ marginBottom: 18 }}>
            {step === "email"
              ? "Use your institute e-mail. We'll send a 6-digit code; there's no password."
              : `We sent a code to ${email}.`}
          </p>

          {step === "email" ? (
            <form className="stack" onSubmit={(e) => { e.preventDefault(); void requestCode(email); }}>
              <label className="field">
                Institute e-mail
                <input type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="name_rollno@nitc.ac.in" autoComplete="email" />
              </label>
              <Button variant="primary" size="big" busy={busy === "email"} type="submit">Send code</Button>
            </form>
          ) : (
            <form className="stack" onSubmit={verify}>
              {hint && <div className="callout signal small">{hint}</div>}
              <label className="field">
                6-digit code
                <input ref={codeRef} className="otp" inputMode="numeric" pattern="\d{6}" maxLength={6} required
                  value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoComplete="one-time-code" />
              </label>
              <Button variant="primary" size="big" busy={busy === "code"} type="submit" disabled={code.length !== 6}>Sign in</Button>
              <button type="button" className="btn ghost small" onClick={() => { setStep("email"); setCode(""); }}>
                <ArrowLeft size={14} aria-hidden /> Use a different e-mail
              </button>
            </form>
          )}
          {error && <div className="callout bad" style={{ marginTop: 12 }} role="alert">{error}</div>}

          {(students.length > 0 || cell.length > 0) && step === "email" && (
            <div className="demo">
              <div className="demo-head">
                <Sparkles size={16} aria-hidden />
                <span><strong>Try the demo.</strong> One click signs you in.</span>
              </div>
              {students.length > 0 && (
                <>
                  <div className="demo-group">Team accounts (students)</div>
                  {students.map((a) => (
                    <button key={a.email} className="demo-card" disabled={busy !== null} onClick={() => void quickSignIn(a)}>
                      <span className="avatar" aria-hidden>{initials(a.name)}</span>
                      <span className="demo-text">
                        <strong>{a.name}</strong>
                        <span>{a.note}</span>
                      </span>
                      <span className="demo-go">{busy === a.email ? "Signing in…" : "Enter"}</span>
                    </button>
                  ))}
                </>
              )}
              {cell.length > 0 && (
                <>
                  <div className="demo-group">Placement cell</div>
                  {cell.map((a) => (
                    <button key={a.email} className="demo-card" disabled={busy !== null} onClick={() => void quickSignIn(a)}>
                      <span className="avatar cell" aria-hidden><ShieldCheck size={16} /></span>
                      <span className="demo-text">
                        <strong>{a.name}</strong>
                        <span>{a.note}</span>
                      </span>
                      <span className="demo-go">{busy === a.email ? "Signing in…" : "Enter"}</span>
                    </button>
                  ))}
                </>
              )}
              <p className="small faint" style={{ marginTop: 10 }}>
                Recruiters and interview panels don't sign in. Sign in as the Placement Cell Admin, open a drive, then Links and offers → Open as recruiter.
              </p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
