import { Routes, Route, Navigate } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import AccountsPage from "./pages/Accounts";
import UploadPage from "./pages/Upload";
import SchedulesPage from "./pages/Schedules";
import LoginPage from "./pages/Login";
import HistoriesPage from "./pages/Histories";
import SettingsPage from "./pages/Settings";

export default function App() {
  return (
    <div className="min-h-screen flex">
      <Sidebar />
      <main className="flex-1 p-8 max-w-6xl">
        <Routes>
          <Route path="/" element={<Navigate to="/accounts" replace />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/schedules" element={<SchedulesPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/login/:sessionId" element={<LoginPage />} />
          <Route path="/histories" element={<HistoriesPage />} />
          <Route path="/videos" element={<Navigate to="/histories" replace />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
