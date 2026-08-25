import { NavLink } from "react-router-dom";
import {
  Users,
  Calendar,
  Download,
  History,
  LayoutDashboard,
  Settings as SettingsIcon,
} from "lucide-react";
import clsx from "clsx";

const items = [
  { to: "/", label: "Tổng quan", icon: LayoutDashboard },
  { to: "/accounts", label: "Tài khoản", icon: Users },
  { to: "/schedules", label: "Lịch đăng", icon: Calendar },
  { to: "/downloads", label: "Tải video", icon: Download },
  { to: "/histories", label: "Lịch sử", icon: History },
  { to: "/settings", label: "Cài đặt", icon: SettingsIcon },
];

export default function Sidebar() {
  return (
    <aside className="w-60 shrink-0 border-r border-slate-200 bg-white">
      <div className="p-5 border-b border-slate-200">
        <h1 className="text-lg font-semibold tracking-tight">TikTok Uploader</h1>
        <p className="text-xs text-slate-500 mt-1">by Minh Tang</p>
      </div>
      <nav className="p-3 space-y-1">
        {items.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              clsx(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-brand-50 text-brand-700"
                  : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
              )
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
