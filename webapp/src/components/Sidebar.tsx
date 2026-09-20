import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Users,
  Calendar,
  ChevronLeft,
  ChevronRight,
  Download,
  History,
  LayoutDashboard,
  Search,
  Settings as SettingsIcon,
} from "lucide-react";
import clsx from "clsx";

const items = [
  { to: "/", label: "Tổng quan", icon: LayoutDashboard },
  { to: "/discovery", label: "Tìm kênh", icon: Search },
  { to: "/schedules", label: "Lịch đăng", icon: Calendar },
  { to: "/downloads", label: "Tải video", icon: Download },
  { to: "/accounts", label: "Tài khoản", icon: Users },
  { to: "/histories", label: "Lịch sử", icon: History },
  { to: "/settings", label: "Cài đặt", icon: SettingsIcon },
];

const STORAGE_KEY = "sidebar-collapsed";

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(STORAGE_KEY) === "1",
  );

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  return (
    <aside
      className={clsx(
        "shrink-0 border-r border-slate-200 bg-white transition-[width] duration-150",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <div
        className={clsx(
          "relative flex items-center border-b border-slate-200 p-5",
          collapsed && "justify-center px-2",
        )}
      >
        {collapsed ? (
          <span className="text-lg font-semibold tracking-tight">TU</span>
        ) : (
          <div>
            <h1 className="text-lg font-semibold tracking-tight">TikTok Uploader</h1>
            <p className="text-xs text-slate-500 mt-1">by Minh Tang</p>
          </div>
        )}
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "Mở rộng sidebar" : "Thu gọn sidebar"}
          className="absolute top-1/2 -right-3 -translate-y-1/2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 shadow-sm hover:bg-slate-50 hover:text-slate-900"
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>
      <nav className="p-3 space-y-1">
        {items.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            title={collapsed ? label : undefined}
            className={({ isActive }) =>
              clsx(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                collapsed && "justify-center px-0",
                isActive
                  ? "bg-brand-50 text-brand-700"
                  : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
              )
            }
          >
            <Icon size={18} />
            {!collapsed && label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
