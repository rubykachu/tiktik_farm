import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Pause, Play, Play as RunNow, Pencil, Search, Trash2 } from "lucide-react";
import { Pipelines, Pipeline, LastRunStatus } from "../api/pipelines";
import { Accounts } from "../api/client";
import { toast } from "sonner";
import ConfirmDialog from "./ConfirmDialog";
import PipelineRunsExpand from "./PipelineRunsExpand";
import { asUtcDate, formatDateTime, formatRelative } from "../utils/datetime";

type Props = {
  onEdit?: (pipeline: Pipeline) => void;
};

type ViewFilter = "all" | "active" | "paused" | "attention";
type SortKey = "name" | "next_fire" | "last_run";

const stateClass = (s: string) =>
  s === "active"
    ? "bg-emerald-100 text-emerald-700"
    : "bg-amber-100 text-amber-700";

const UNIT_VI: Record<string, string> = { minutes: "phút", hours: "giờ", days: "ngày", weeks: "tuần" };

function intervalLabel(p: Pipeline): string {
  const unit = UNIT_VI[p.interval_unit] ?? p.interval_unit;
  return `${p.interval_value} ${unit}`;
}

function channelLabel(p: Pipeline): string {
  const url = p.channel_url;
  if (p.source_type !== "douyin") {
    const m = url.match(/@[\w.-]+/);
    if (m) return m[0];
  }
  return url.length > 9 ? "…" + url.slice(-8) : url;
}

const RUN_TONE: Record<LastRunStatus, string> = {
  ok: "text-emerald-600",
  partial: "text-amber-600",
  failed: "text-red-600",
  error: "text-red-600",
  running: "text-blue-600",
};

const RUN_ICON: Record<LastRunStatus, string> = {
  ok: "✅",
  partial: "⚠️",
  failed: "❌",
  error: "🛑",
  running: "⏳",
};

const RUN_LABEL: Record<LastRunStatus, string> = {
  ok: "thành công",
  partial: "một phần",
  failed: "thất bại",
  error: "lỗi hệ thống",
  running: "đang chạy",
};

/** A run worth the operator's attention, as opposed to one that just worked. */
function needsAttention(p: Pipeline): boolean {
  const s = p.last_run?.status;
  return s === "failed" || s === "error" || s === "partial";
}

function LastRunCell({ p }: { p: Pipeline }) {
  const run = p.last_run;
  if (!run) return <span className="text-slate-400">chưa chạy</span>;
  const detail =
    run.status === "error"
      ? run.error_text
      : run.status === "partial" || run.status === "failed"
        ? `${run.failed_count} lỗi`
        : null;
  return (
    <span title={formatDateTime(run.at)}>
      <span className="whitespace-nowrap">
        <span className={RUN_TONE[run.status]}>
          {RUN_ICON[run.status]} {RUN_LABEL[run.status]}
        </span>
        <span className="text-slate-400"> · {formatRelative(run.at)}</span>
      </span>
      {detail && (
        <div className="text-xs text-slate-500 truncate" title={detail}>
          {detail}
        </div>
      )}
    </span>
  );
}

function accountIds(p: Pipeline): number[] {
  try {
    return JSON.parse(p.account_ids_json) as number[];
  } catch {
    return [];
  }
}

export default function PipelinesTable({ onEdit }: Props) {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [view, setView] = useState<ViewFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("next_fire");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["pipelines"],
    queryFn: Pipelines.list,
    refetchInterval: 5000,
  });

  const { data: accountsData } = useQuery({
    queryKey: ["accounts"],
    queryFn: Accounts.list,
  });

  const accountLabel = useMemo(() => {
    const m = new Map<number, string>();
    for (const a of accountsData ?? []) {
      m.set(a.id, (a.display_name || a.username).trim() || a.username);
    }
    return m;
  }, [accountsData]);

  const [deleteTarget, setDeleteTarget] = useState<Pipeline | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const toggleState = useMutation({
    mutationFn: (p: Pipeline) =>
      Pipelines.update(p.id, {
        state: p.state === "active" ? "paused" : "active",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pipelines"] }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => Pipelines.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pipelines"] });
      qc.invalidateQueries({ queryKey: ["videos"] });
      toast.success(`Đã xóa lịch "${deleteTarget?.name ?? ""}"`, {
        description: "Lịch sử chạy và đăng tải đã được xóa.",
      });
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 409 && detail?.code === "PIPELINE_RUNNING") {
        toast.error("Lịch đang chạy", {
          description: "Hãy dừng lại và chờ lần chạy hiện tại kết thúc trước khi xóa.",
        });
      } else {
        toast.error("Xóa thất bại", { description: e.message });
      }
      setDeleteTarget(null);
    },
  });

  const runNowMut = useMutation({
    mutationFn: (id: number) => Pipelines.runNow(id),
    onSuccess: () => {
      toast.success("Đã xếp hàng", { description: "Lịch sẽ chạy ở lần kế tiếp." });
      qc.invalidateQueries({ queryKey: ["pipelines"] });
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      if (detail?.code === "PIPELINE_PAUSED") {
        toast.error("Lịch đang tạm dừng", { description: detail?.message ?? "Hãy tiếp tục trước." });
      } else if (detail?.code === "PIPELINE_RUNNING") {
        toast.error("Lịch đang chạy", { description: "Chờ lần chạy hiện tại kết thúc." });
      } else if (detail?.code === "RUN_NOW_QUEUED") {
        toast.info("Đã xếp hàng rồi", { description: "Chờ lần chạy kế tiếp." });
      } else {
        toast.error("Lỗi", { description: e.message });
      }
    },
  });

  function isPipelineLocked(p: Pipeline): boolean {
    if (!p.lock_until) return false;
    return new Date(p.lock_until).getTime() > Date.now();
  }

  function isRunNowQueued(p: Pipeline): boolean {
    return p.run_now_requested_at != null;
  }

  const activeCount = data?.filter((p) => p.state === "active").length ?? 0;
  const attentionCount = data?.filter(needsAttention).length ?? 0;

  const visible = useMemo(() => {
    let rows = data ?? [];
    const q = search.trim().toLowerCase();
    if (q) {
      rows = rows.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.channel_url.toLowerCase().includes(q),
      );
    }
    if (view === "active") rows = rows.filter((p) => p.state === "active");
    if (view === "paused") rows = rows.filter((p) => p.state === "paused");
    if (view === "attention") rows = rows.filter(needsAttention);

    const dir = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (sortKey === "name") return dir * a.name.localeCompare(b.name);
      if (sortKey === "last_run") {
        // Never-run sorts last in either direction: it is the absence of an
        // answer, not the oldest one.
        const av = a.last_run?.at ? asUtcDate(a.last_run.at).getTime() : null;
        const bv = b.last_run?.at ? asUtcDate(b.last_run.at).getTime() : null;
        if (av === null) return 1;
        if (bv === null) return -1;
        return dir * (av - bv);
      }
      // next_fire: a paused pipeline has no upcoming run, so it sorts last too.
      const av = a.state === "active" ? asUtcDate(a.next_fire_at).getTime() : null;
      const bv = b.state === "active" ? asUtcDate(b.next_fire_at).getTime() : null;
      if (av === null) return 1;
      if (bv === null) return -1;
      return dir * (av - bv);
    });
  }, [data, search, view, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function SortHeader({ label, sortKey: key }: { label: string; sortKey: SortKey }) {
    const active = sortKey === key;
    return (
      <button
        className="flex items-center gap-1 uppercase tracking-wide hover:text-slate-700"
        onClick={() => toggleSort(key)}
      >
        {label}
        {active &&
          (sortDir === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}
      </button>
    );
  }

  return (
    <>
      {activeCount > 5 && (
        <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {activeCount} lịch đang chạy. Hệ thống có thể chậm lại.
          Nên tạm dừng bớt một số.
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search
            size={14}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
          />
          <input
            className="input w-64 pl-8"
            placeholder="Tìm theo tên hoặc kênh…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex rounded border overflow-hidden text-sm">
          {([
            ["all", `Tất cả (${data?.length ?? 0})`],
            ["attention", `Cần xem (${attentionCount})`],
            ["active", `Đang chạy (${activeCount})`],
            ["paused", "Tạm dừng"],
          ] as [ViewFilter, string][]).map(([key, label]) => (
            <button
              key={key}
              className={
                "px-3 py-1.5 border-r last:border-r-0 " +
                (view === key
                  ? "bg-brand-50 text-brand-700 font-medium"
                  : "text-slate-600 hover:bg-slate-50")
              }
              onClick={() => setView(key)}
            >
              {label}
            </button>
          ))}
        </div>
        {(search || view !== "all") && (
          <button
            className="btn-secondary text-sm"
            onClick={() => {
              setSearch("");
              setView("all");
            }}
          >
            Xóa bộ lọc
          </button>
        )}
        <span className="ml-auto text-sm text-slate-500 tabular-nums">
          {visible.length}/{data?.length ?? 0} lịch
        </span>
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm table-fixed">
          <colgroup>
            <col className="w-[15%]" />
            <col className="w-[12%]" />
            <col className="w-[9%]" />
            <col className="w-[7%]" />
            <col className="w-[12%]" />
            <col className="w-[16%]" />
            <col className="w-[9%]" />
            <col className="w-[18%]" />
          </colgroup>
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="text-left p-3">
                <SortHeader label="Tên" sortKey="name" />
              </th>
              <th className="text-left p-3">Kênh</th>
              <th className="text-left p-3">Tài khoản</th>
              <th className="text-left p-3">Chu kỳ</th>
              <th className="text-left p-3">
                <SortHeader label="Lần chạy tới" sortKey="next_fire" />
              </th>
              <th className="text-left p-3">
                <SortHeader label="Lần chạy gần nhất" sortKey="last_run" />
              </th>
              <th className="text-left p-3">Trạng thái</th>
              <th className="p-3 text-right">Thao tác</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr>
                <td className="p-4 text-slate-500" colSpan={8}>
                  Đang tải…
                </td>
              </tr>
            )}
            {isError && (
              <tr>
                <td className="p-4 text-center text-red-400" colSpan={8}>
                  Không thể tải. Kiểm tra kết nối.
                </td>
              </tr>
            )}
            {!isLoading && !isError && visible.length === 0 && (
              <tr>
                <td className="p-6 text-center text-slate-500" colSpan={8}>
                  {(data?.length ?? 0) === 0
                    ? 'Chưa có lịch đăng nào. Bấm "Tạo lịch mới" để hệ thống bắt đầu tự động lấy video và đăng lên TikTok.'
                    : "Không có lịch nào khớp bộ lọc."}
                </td>
              </tr>
            )}
            {visible.flatMap((p) => {
              const rows = [
                <tr
                  key={p.id}
                  className="hover:bg-slate-50 cursor-pointer"
                  onClick={() => setExpandedId(expandedId === p.id ? null : p.id)}
                >
                  <td className="p-3 font-medium">{p.name}</td>
                  <td className="p-3 text-slate-600 max-w-xs truncate">
                    <a
                      href={p.channel_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline"
                      title={p.channel_url}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {channelLabel(p)}
                    </a>
                  </td>
                  <td className="p-3 text-slate-600 text-xs">
                    {(() => {
                      const names = accountIds(p).map(
                        (id) => accountLabel.get(id) ?? `#${id}`,
                      );
                      if (names.length === 0) return "—";
                      // Truncating several names to 8 chars renders them
                      // identical ("USA Clip…, USA Clip…"), which says less
                      // than the count does. Full list stays in the tooltip.
                      const shown =
                        names.length === 1 ? names[0] : `${names.length} tài khoản`;
                      return <span title={names.join(", ")}>{shown}</span>;
                    })()}
                  </td>
                  <td className="p-3 text-slate-600">{intervalLabel(p)}</td>
                  <td className="p-3 text-slate-600 text-xs">
                    {/* A paused pipeline keeps its next_fire_at but will not
                        fire, so showing a countdown would be a lie. */}
                    {p.state === "active" ? (
                      <span title={formatDateTime(p.next_fire_at)}>
                        {formatRelative(p.next_fire_at)}
                      </span>
                    ) : (
                      <span className="text-slate-400">đang tạm dừng</span>
                    )}
                  </td>
                  <td className="p-3 text-xs">
                    <LastRunCell p={p} />
                  </td>
                  <td className="p-3">
                    <span className={"chip " + stateClass(p.state)}>{p.state === "active" ? "hoạt động" : "tạm dừng"}</span>
                  </td>
                  <td className="p-3 text-right space-x-2 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                    {(() => {
                      const locked = isPipelineLocked(p);
                      const queued = isRunNowQueued(p);
                      if (p.state === "paused") {
                        return (
                          <button
                            className="text-xs text-slate-600"
                            onClick={() => toggleState.mutate(p)}
                          >
                            <Play size={14} className="inline" /> Tiếp tục
                          </button>
                        );
                      }
                      if (locked) {
                        return (
                          <span className="text-xs text-slate-500" title="Đang xử lý lần này">
                            Đang chạy…
                          </span>
                        );
                      }
                      if (queued) {
                        return (
                          <>
                            <button
                              type="button"
                              disabled
                              className="text-xs text-slate-400 cursor-not-allowed"
                              title="Sẽ chạy ở lần kế tiếp"
                            >
                              Chờ chạy…
                            </button>
                            <button
                              className="text-xs text-slate-600"
                              title="Tạm dừng lịch"
                            onClick={() => window.confirm(`Tạm dừng lịch "${p.name}"?`) && toggleState.mutate(p)}
                            >
                              <Pause size={14} className="inline" /> Dừng
                            </button>
                          </>
                        );
                      }
                      return (
                        <>
                          <button
                            className="text-xs text-slate-600"
                            title="Chạy ngay, không đợi lịch"
                            onClick={() => window.confirm(`Chạy ngay lịch "${p.name}"?`) && runNowMut.mutate(p.id)}
                          >
                            <RunNow size={14} className="inline" /> Chạy
                          </button>
                          <button
                            className="text-xs text-slate-600"
                            title="Tạm dừng lịch"
                            onClick={() => window.confirm(`Tạm dừng lịch "${p.name}"?`) && toggleState.mutate(p)}
                          >
                            <Pause size={14} className="inline" /> Dừng
                          </button>
                        </>
                      );
                    })()}
                    {/* Icon-only: these two are used rarely, and the labels
                        were crowding out the run/pause action that is not. */}
                    {onEdit && (
                      <button
                        className="text-brand-600 hover:text-brand-800 p-1"
                        title="Sửa lịch"
                        aria-label="Sửa lịch"
                        onClick={() => onEdit(p)}
                      >
                        <Pencil size={14} />
                      </button>
                    )}
                    <button
                      className="text-slate-400 hover:text-red-600 p-1"
                      title="Xóa lịch"
                      aria-label="Xóa lịch"
                      onClick={() => setDeleteTarget(p)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>,
              ];
              if (expandedId === p.id) {
                rows.push(
                  <tr key={`${p.id}-expand`}>
                    <td colSpan={8} className="p-0">
                      <PipelineRunsExpand pipelineId={p.id} />
                    </td>
                  </tr>
                );
              }
              return rows;
            })}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`Xóa lịch "${deleteTarget?.name ?? ""}"?`}
        body={
          <p>
            Toàn bộ lịch sử chạy và đăng tải sẽ bị xóa vĩnh viễn. Không thể hoàn tác.
            Nếu lịch đang chạy, hãy dừng lại trước.
          </p>
        }
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />
    </>
  );
}
