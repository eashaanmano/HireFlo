import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { CellShell, StudentShell } from "./components/Shell";
import { Loading } from "./components/ui";
import { AuthProvider, homeFor, useAuth } from "./lib/auth";
import { CheckIn } from "./pages/CheckIn";
import { Login } from "./pages/Login";
import { CellAudit } from "./pages/cell/Audit";
import { CellAi } from "./pages/cell/AiAssistant";
import { CellCompanies } from "./pages/cell/Companies";
import { ControlRoomPage } from "./pages/cell/ControlRoom";
import { CellDrive } from "./pages/cell/Drive";
import { CellDrives } from "./pages/cell/Drives";
import { CellInbox } from "./pages/cell/Inbox";
import { CellInsights } from "./pages/cell/Insights";
import { CellJnfReview } from "./pages/cell/JnfReview";
import { CellPolicy } from "./pages/cell/Policy";
import { CellSimulator } from "./pages/cell/Simulator";
import { CellStudents } from "./pages/cell/Students";
import { CellUsers } from "./pages/cell/Users";
import { PanelView } from "./pages/panel/PanelView";
import { JobNotificationForm } from "./pages/recruiter/JobNotificationForm";
import { RecruiterDrive } from "./pages/recruiter/RecruiterDrive";
import { StudentAsk } from "./pages/student/Ask";
import { StudentDrive } from "./pages/student/Drive";
import { StudentDrives } from "./pages/student/Drives";
import { StudentHome } from "./pages/student/Home";
import { StudentOffers } from "./pages/student/Offers";
import { StudentProfile } from "./pages/student/Profile";
import { StudentSchedule } from "./pages/student/Schedule";

function Guard({ role, children }: { role: "student" | "cell"; children: ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return <div className="main"><Loading /></div>;
  if (!me) return <Navigate to={`/login?next=${encodeURIComponent(window.location.pathname)}`} replace />;
  const isStudent = me.role === "student";
  if ((role === "student") !== isStudent) return <Navigate to={homeFor(me)} replace />;
  return <>{children}</>;
}

function Home() {
  const { me, loading } = useAuth();
  if (loading) return <div className="main"><Loading /></div>;
  return <Navigate to={homeFor(me)} replace />;
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route path="/checkin" element={<CheckIn />} />
          <Route path="/r/:token" element={<RecruiterDrive />} />
          <Route path="/p/:token" element={<PanelView />} />
          <Route path="/j/:token" element={<JobNotificationForm />} />
          <Route path="/student" element={<Guard role="student"><StudentShell /></Guard>}>
            <Route index element={<StudentHome />} />
            <Route path="drives" element={<StudentDrives />} />
            <Route path="drives/:id" element={<StudentDrive />} />
            <Route path="schedule" element={<StudentSchedule />} />
            <Route path="offers" element={<StudentOffers />} />
            <Route path="ask" element={<StudentAsk />} />
            <Route path="profile" element={<StudentProfile />} />
          </Route>
          <Route path="/cell" element={<Guard role="cell"><CellShell /></Guard>}>
            <Route index element={<CellInbox />} />
            <Route path="drives" element={<CellDrives />} />
            <Route path="drives/:id" element={<CellDrive />} />
            <Route path="drives/:id/control" element={<ControlRoomPage />} />
            <Route path="students" element={<CellStudents />} />
            <Route path="companies" element={<CellCompanies />} />
            <Route path="jnf/:id" element={<CellJnfReview />} />
            <Route path="insights" element={<CellInsights />} />
            <Route path="policy" element={<CellPolicy />} />
            <Route path="simulator" element={<CellSimulator />} />
            <Route path="audit" element={<CellAudit />} />
            <Route path="users" element={<CellUsers />} />
            <Route path="ai" element={<CellAi />} />
          </Route>
          <Route path="*" element={<Home />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
