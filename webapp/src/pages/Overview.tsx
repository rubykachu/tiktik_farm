import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Download,
  KeyRound,
  Play,
  XCircle,
} from "lucide-react";
import { OverviewApi, type Overview } from "../api/overview";
import { formatDateTime, formatRelative } from "../utils/datetime";

const POLL_MS = 15000;

type Problem = { text: string; to: string; action: string };

/**
 * The headline answers "is anything wrong?" before the operator reads a single
 * number. Problems are ordered by how much they cost while unnoticed: a dead
 * tick stops everything, a bad cookie stops one account, a failed upload is
 * already in the past.
 */
function collectProblems(d: Overview): Problem[] {
  const out: Problem[] = [];
  if (d.stuck_runs > 0) {
    out.push({
      text: `${d.stuck_runs} lần chạy bị treo — scheduler có thể đã dừng giữa chừng`,
      to: "/schedules",
      action: "Xem lịch đăng",
    });
  }
  if (d.accounts.invalid > 0) {
    const names = d.accounts.invalid_usernames.join(", ");
    out.push({
      text: `${d.accounts.invalid} tài khoản hỏng cookie${names ? `: ${names}` : ""}`,
      to: "/accounts",
      action: "Nhập lại cookie",
    });
  }
  if (d.pipelines.total > 0 && d.pipelines.active === 0) {
    out.push({
      text: "Không có lịch đăng nào đang chạy",
      to: "/schedules",
      action: "Bật lịch đăng",
    });
  }
  if (d.uploads_24h.failed > 0) {
    out.push({
      text: `${d.uploads_24h.failed} video đăng lỗi trong 24 giờ qua`,
      to: "/histories",
      action: "Xem lịch sử",
    });
  }
  return out;
}

function Tile({
  icon: Icon,
  label,
  value,
  sub,
  tone = "normal",
  to,
}: {
  icon: typeof Play;
  label: string;
  value: string;
  sub?: string;
  tone?: "normal" | "good" | "warn";
  to?: string;
}) {
  const toneClass =
    tone === "good"
      ? "text-emerald-600"
      : tone === "warn"
        ? "text-amber-600"
        : "text-slate-900";

  const body = (
    <div className="border rounded-lg p-4 bg-white h-full hover:border-slate-300 transition-colors">
      <div className="flex items-center gap-2 text-xs font-medium text-slate-500 uppercase tracking-wide">
        <Icon size={14} />
        {label}
      </div>
      <div className={`text-2xl font-semibold mt-2 tabular-nums ${toneClass}`}>
        {value}
      </div>
      {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
    </div>
  );

  return to ? (
    <Link to={to} className="block">
      {body}
    </Link>
  ) : (
    body
  );
}

export default function OverviewPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["overview"],
    queryFn: OverviewApi.get,
    refetchInterval: POLL_MS,
  });

  if (isLoading) {
    return <p className="text-sm text-slate-500">Đang tải…</p>;
  }
  if (isError || !data) {
    return (
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight">Tổng quan</h2>
        <p className="text-sm text-red-600">
          Không đọc được trạng thái hệ thống. Kiểm tra API còn chạy không.
        </p>
      </div>
    );
  }

  const problems = collectProblems(data);
  const { succeeded, failed } = data.uploads_24h;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Tổng quan</h2>
        <p className="text-sm text-slate-500 mt-1">
          Cập nhật {formatRelative(data.generated_at)}.
        </p>
      </div>

      {problems.length === 0 ? (
        <div className="border border-emerald-200 bg-emerald-50 rounded-lg p-4 flex items-start gap-3">
          <CheckCircle2 className="text-emerald-600 shrink-0 mt-0.5" size={20} />
          <div>
            <p className="font-medium text-emerald-900">Mọi thứ đang chạy bình thường</p>
            <p className="text-sm text-emerald-800 mt-0.5">
              {data.pipelines.active > 0
                ? `${data.pipelines.active} lịch đăng đang hoạt động. Lần chạy tới ${formatRelative(data.next_fire_at)}.`
                : "Chưa có lịch đăng nào. Tạo lịch để bắt đầu."}
            </p>
          </div>
        </div>
      ) : (
        <div className="border border-amber-200 bg-amber-50 rounded-lg p-4">
          <div className="flex items-center gap-2 font-medium text-amber-900">
            <AlertTriangle size={18} />
            Cần xử lý ({problems.length})
          </div>
          <ul className="mt-2 space-y-1.5">
            {problems.map((p) => (
              <li
                key={p.text}
                className="text-sm text-amber-900 flex items-center justify-between gap-4"
              >
                <span>{p.text}</span>
                <Link
                  to={p.to}
                  className="text-amber-900 underline shrink-0 whitespace-nowrap hover:no-underline"
                >
                  {p.action}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Tile
          icon={CheckCircle2}
          label="Đăng thành công 24h"
          value={String(succeeded)}
          sub={succeeded + failed === 0 ? "Chưa đăng video nào" : undefined}
          tone={succeeded > 0 ? "good" : "normal"}
          to="/histories"
        />
        <Tile
          icon={XCircle}
          label="Đăng lỗi 24h"
          value={String(failed)}
          tone={failed > 0 ? "warn" : "normal"}
          to="/histories"
        />
        <Tile
          icon={Play}
          label="Lịch đang chạy"
          value={`${data.pipelines.active}/${data.pipelines.total}`}
          sub={data.pipelines.total === 0 ? "Chưa tạo lịch nào" : undefined}
          to="/schedules"
        />
        <Tile
          icon={Clock}
          label="Lần chạy tới"
          value={formatRelative(data.next_fire_at)}
          sub={data.next_fire_at ? formatDateTime(data.next_fire_at) : undefined}
          to="/schedules"
        />
        <Tile
          icon={KeyRound}
          label="Tài khoản TikTok"
          value={`${data.accounts.total - data.accounts.invalid}/${data.accounts.total}`}
          sub="còn dùng được"
          tone={data.accounts.invalid > 0 ? "warn" : "normal"}
          to="/accounts"
        />
        <Tile
          icon={Download}
          label="Video chờ tải về"
          value={String(data.downloads_ready)}
          sub={data.downloads_ready > 0 ? "Tự xoá sau 24 giờ" : undefined}
          to="/downloads"
        />
      </div>

      {data.recent_errors.length > 0 && (
        <div>
          <h3 className="text-base font-semibold text-slate-800 border-b pb-1 mb-2">
            Lỗi gần đây
          </h3>
          <div className="border rounded overflow-hidden">
            <table className="w-full text-sm">
              <tbody>
                {data.recent_errors.map((e, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td className="p-2 text-slate-500 whitespace-nowrap w-32">
                      {formatRelative(e.at)}
                    </td>
                    <td className="p-2">{e.title || "—"}</td>
                    <td className="p-2 text-red-600">{e.error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
