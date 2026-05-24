import { Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import Sidebar from "./components/Sidebar";
import AccountsPage from "./pages/Accounts";
import SchedulesPage from "./pages/Schedules";
import HistoriesPage from "./pages/Histories";
import SettingsPage from "./pages/Settings";

export default function App() {
  return (
    <div className="min-h-screen flex">
      <Toaster position="top-right" richColors closeButton />
      <Sidebar />
      <main className="flex-1 p-8 max-w-6xl">
        <Routes>
          <Route path="/" element={<Navigate to="/accounts" replace />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/schedules" element={<SchedulesPage />} />
          <Route path="/histories" element={<HistoriesPage />} />
          <Route path="/videos" element={<Navigate to="/histories" replace />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
