import { useQuery } from "@tanstack/react-query";
import type { LucideIcon } from "lucide-react";
import {
  Building2,
  CalendarClock,
  ClipboardList,
  FlaskConical,
  Gauge,
  Gift,
  History,
  Inbox,
  LayoutDashboard,
  ListChecks,
  MessageCircleQuestion,
  Scale,
  Sparkles,
  User,
  UserCog,
  Users,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { InboxItem } from "../api/types";
import { AssistantLauncher, CELL_SUGGESTIONS, STUDENT_SUGGESTIONS } from "./Assistant";
import { useAuth } from "../lib/auth";
import { useLive } from "../lib/live";

interface Item {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  count?: number;
}

function Nav({ items }: { items: Item[] }) {
  return (
    <nav aria-label="Main">
      {items.map(({ to, label, icon: Icon, end, count }) => (
        <NavLink key={to} to={to} end={end} className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>
          <Icon size={18} aria-hidden />
          <span>{label}</span>
          {count ? <span className="count">{count}</span> : null}
        </NavLink>
      ))}
    </nav>
  );
}

function Frame({ items, assistant }: { items: Item[]; assistant: "student" | "cell" }) {
  const { me, logout } = useAuth();
  const live = useLive(Boolean(me));
  const { pathname } = useLocation();
  const onChatPage = pathname === "/student/ask" || pathname === "/cell/ai";
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <img src="/icon.svg" alt="" />
          <span>
            HireFlo <span style={{ fontWeight: 500, fontSize: "0.78em", opacity: 0.7 }}>(DriveOps)</span>
          </span>
        </div>
        <Nav items={items} />
        <div className="who">
          <div>
            <strong>{me?.name}</strong>
          </div>
          <div className="faint">{me?.email}</div>
          <div className="row between" style={{ marginTop: 8 }}>
            <span className={`live-dot ${live === "live" ? "live" : ""}`}>{live === "live" ? "Live" : "Reconnecting"}</span>
            <button className="btn ghost small" onClick={() => void logout()}>
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
      {!onChatPage && (assistant === "student" ? (
        <AssistantLauncher endpoint="/api/me/assistant" suggestions={STUDENT_SUGGESTIONS} storageKey="hireflo.chat.student"
          intro="Ask about your drives, eligibility, interviews, offers or the placement rules. Answers use your own HireFlo data." />
      ) : (
        <AssistantLauncher endpoint="/api/cell/assistant" suggestions={CELL_SUGGESTIONS} storageKey="hireflo.chat.cell"
          intro="Ask about the season: placements, branches, drives, deadlines, the forecast or the inbox. Only totals are shared with the AI, never individual students." />
      ))}
    </div>
  );
}

export function StudentShell() {
  const items: Item[] = [
    { to: "/student", label: "Home", icon: LayoutDashboard, end: true },
    { to: "/student/drives", label: "Drives", icon: ListChecks },
    { to: "/student/schedule", label: "Timeline", icon: CalendarClock },
    { to: "/student/offers", label: "Offers", icon: Gift },
    { to: "/student/ask", label: "Assistant", icon: MessageCircleQuestion },
    { to: "/student/profile", label: "Profile", icon: User },
  ];
  return <Frame items={items} assistant="student" />;
}

export function CellShell() {
  const { me } = useAuth();
  const inbox = useQuery({ queryKey: ["cell", "inbox"], queryFn: () => api.get<InboxItem[]>("/api/cell/inbox") });
  const urgent = inbox.data?.filter((i) => i.severity !== "low").length ?? 0;
  const items: Item[] = [
    { to: "/cell", label: "Inbox", icon: Inbox, end: true, count: urgent },
    { to: "/cell/drives", label: "Drives", icon: ClipboardList },
    { to: "/cell/students", label: "Students", icon: Users },
    { to: "/cell/companies", label: "Companies", icon: Building2 },
    { to: "/cell/insights", label: "Insights", icon: Gauge },
    { to: "/cell/policy", label: "Policy", icon: Scale },
    { to: "/cell/simulator", label: "Simulator", icon: FlaskConical },
    { to: "/cell/audit", label: "Audit log", icon: History },
    { to: "/cell/ai", label: "AI assistant", icon: Sparkles },
    ...(me?.role === "admin" ? [{ to: "/cell/users", label: "Team", icon: UserCog }] : []),
  ];
  return <Frame items={items} assistant="cell" />;
}
