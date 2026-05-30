import { Fragment, useMemo, useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, ChevronDown, ChevronRight, Trash2 } from "lucide-react";
import { Accounts } from "../api/client";
import { Pipelines, PipelineRunItem } from "../api/pipelines";
import { statusBadge } from "../utils/statusBadge";
import { toast } from "sonner";

function asUtc(s: string): Date {
  return new Date(/Z|[+-]\d{2}:\d{2}$/.test(s) ? s : s + "Z");
}

function statusLabel(s: string): string {
  if (s === "succeeded") return "thành công";
  if (s === "failed") return "thất bại";
  return s;
}

type ClearRunsFn = (runIds: number[]) => void;

type Props = { pipelineId: number };

function ErrorDetailModal({ text, onClose }: { text: string; onClose: () => void }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl w-full max-w-2xl mx-4 p-4 space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-red-700">Chi tiết lỗi</span>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-lg leading-none">×</button>
        </div>
        <pre className="text-xs bg-slate-900 text-slate-100 rounded p-3 overflow-auto max-h-96 whitespace-pre-wrap break-all font-mono">
          {text}
        </pre>
      </div>
    </div>
  );
}

interface DayGroup {
  date: string; // "YYYY-MM-DD"
  runs: PipelineRunItem[];
  totalUploaded: number;
  latestRun: PipelineRunItem;
}

function groupByDay(runs: PipelineRunItem[]): DayGroup[] {
  const map = new Map<string, PipelineRunItem[]>();
  for (const r of runs) {
    const date = r.started_at ? r.started_at.slice(0, 10) : "unknown";
    if (!map.has(date)) map.set(date, []);
    map.get(date)!.push(r);
  }
  return Array.from(map.entries()).map(([date, dayRuns]) => {
    const sorted = [...dayRuns].sort((a, b) => {
      const ta = a.started_at ?? "";
      const tb = b.started_at ?? "";
      return tb.localeCompare(ta);
    });
    return {
      date,
      runs: dayRuns,
      totalUploaded: dayRuns.reduce((s, r) => s + r.uploaded_count, 0),
      latestRun: sorted[0],
    };
  });
}

function groupStatusLabel(g: DayGroup): { text: string; cls: string } {
  return runStatusLabel(g.latestRun);
}

function runStatusLabel(r: PipelineRunItem): { text: string; cls: string } {
  if (!r.finished_at) {
    if (r.status_text) return { text: r.status_text, cls: "text-blue-700 italic" };
    return { text: "đang chạy", cls: "chip bg-blue-100 text-blue-700" };
  }
  if (r.error_text) return { text: "lỗi", cls: "chip bg-red-100 text-red-700" };
  if (r.pulled_count === 0) return { text: "không có video mới", cls: "text-slate-400 italic" };
  return { text: "hoàn thành", cls: "chip bg-emerald-100 text-emerald-700" };
}

function VideosList({ pipelineId, runId }: { pipelineId: number; runId: number }) {
  const [errorModal, setErrorModal] = useState<string | null>(null);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["run-videos", pipelineId, runId],
    queryFn: () => Pipelines.listRunVideos(pipelineId, runId),
  });

  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: Accounts.list,
  });

  const accountLabel = useMemo(() => {
    const m = new Map<number, string>();
    for (const a of accounts ?? []) {
      m.set(a.id, (a.display_name || a.username).trim() || a.username);
    }
    return m;
  }, [accounts]);

  if (isLoading) return <div className="p-2 text-xs text-slate-400">Đang tải video…</div>;
  if (isError) return <div className="p-2 text-xs text-red-400">Không thể tải video.</div>;
  if (!data?.length) return <div className="p-2 text-xs text-slate-400">Không có video.</div>;

  return (
    <>
      {errorModal && (
        <ErrorDetailModal text={errorModal} onClose={() => setErrorModal(null)} />
      )}
      <div className="ml-10 my-1 rounded border border-slate-200 overflow-hidden bg-white">
        <table className="w-full text-xs border-separate border-spacing-0 table-fixed">
          <colgroup>
            <col className="w-[10%]" />
            <col className="w-[12%]" />
            <col className="w-[22%]" />
            <col className="w-[22%]" />
            <col className="w-[10%]" />
            <col className="w-[9%]" />
            <col className="w-[15%]" />
          </colgroup>
          <thead>
            <tr className="bg-slate-100 text-slate-500 uppercase tracking-wide text-[10px]">
              <th className="text-left px-3 py-1.5 font-medium">Tài khoản</th>
              <th className="text-left px-3 py-1.5 font-medium">Nguồn</th>
              <th className="text-left px-3 py-1.5 font-medium">Title</th>
              <th className="text-left px-3 py-1.5 font-medium">AI Title</th>
              <th className="text-left px-3 py-1.5 font-medium">TikTok</th>
              <th className="text-left px-3 py-1.5 font-medium">Trạng thái</th>
              <th className="text-left px-3 py-1.5 font-medium">Lỗi</th>
            </tr>
          </thead>
          <tbody>
            {data.map((v, i) => {
              const sourceHref = v.douyin_aweme_id
                ? `https://www.douyin.com/video/${v.douyin_aweme_id}`
                : v.youtube_url || null;
              const sourceLabel = v.source_title || v.youtube_video_id || v.douyin_aweme_id || "link";
              return (
                <tr key={v.id} className={i % 2 === 0 ? "bg-white" : "bg-slate-50/60"}>
                  <td className="px-3 py-1.5 text-slate-700 truncate">{accountLabel.get(v.account_id) ?? `#${v.account_id}`}</td>
                  <td className="px-3 py-1.5 truncate">
                    {sourceHref ? (
                      <a href={sourceHref} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline" title={String(sourceLabel)}>
                        {sourceLabel}
                      </a>
                    ) : (
                      <span className="text-slate-400">{sourceLabel}</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 truncate text-slate-700">
                    {v.source_title
                      ? <button className="truncate w-full text-left hover:text-blue-600 cursor-pointer" onClick={() => setErrorModal(v.source_title!)}>{v.source_title}</button>
                      : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-3 py-1.5 truncate text-slate-700">
                    {v.ai_title
                      ? <button className="truncate w-full text-left hover:text-blue-600 cursor-pointer" onClick={() => setErrorModal(v.ai_title!)}>{v.ai_title}</button>
                      : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-3 py-1.5 truncate">
                    {v.tiktok_url ? (
                      <a href={v.tiktok_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline" title={v.tiktok_video_id || "link"}>
                        {v.tiktok_video_id || "link"}
                      </a>
                    ) : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-3 py-1.5">
                    <span className={"chip text-[10px] " + statusBadge(v.status)}>{statusLabel(v.status)}</span>
                  </td>
                  <td className="px-3 py-1.5">
                    {v.error_text ? (
                      <button
                        className="max-w-[220px] truncate text-red-500 hover:text-red-700 hover:underline text-left"
                        title={v.error_text}
                        onClick={() => setErrorModal(v.error_text || "")}
                      >
                        {v.error_text}
                      </button>
                    ) : <span className="text-slate-300">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function RunRow({
  pipelineId,
  run,
  expanded,
  onToggle,
}: {
  pipelineId: number;
  run: PipelineRunItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  const [errorModal, setErrorModal] = useState<string | null>(null);
  const status = runStatusLabel(run);
  const isError = status.text === "lỗi";

  return (
    <Fragment>
      {errorModal && (
        <ErrorDetailModal text={errorModal} onClose={() => setErrorModal(null)} />
      )}
      <tr
        className={`cursor-pointer transition-colors border-l-4 ${expanded ? "bg-blue-50 border-blue-300" : "bg-white hover:bg-slate-50 border-slate-200"}`}
        onClick={onToggle}
      >
        <td className="py-1.5 pl-8 pr-1 text-slate-400">
          {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        </td>
        <td className="py-1.5 px-2 text-slate-600 tabular-nums">
          {run.started_at ? asUtc(run.started_at).toLocaleTimeString() : "—"}
        </td>
        <td className="py-1.5 px-2 tabular-nums">{run.pulled_count}</td>
        <td className="py-1.5 px-2 tabular-nums">{run.uploaded_count}</td>
        <td className="py-1.5 px-2 tabular-nums">{run.skipped_dup_count + run.skipped_exhausted_count}</td>
        <td className="py-1.5 px-2 tabular-nums">{run.failed_count}</td>
        <td className="py-1.5 px-2 text-[10px]">
          {isError ? (
            <button
              className={`${status.cls} cursor-pointer hover:opacity-75`}
              onClick={(e) => {
                e.stopPropagation();
                setErrorModal(run.error_text || "");
              }}
            >
              {status.text}
            </button>
          ) : (
            <span className={status.cls}>{status.text}</span>
          )}
        </td>
        <td className="py-1.5 px-1 w-5 text-center">
          {run.pushed_notify_at ? (
            <span title={asUtc(run.pushed_notify_at).toLocaleString()}>
              <Bell size={10} className="text-blue-400 inline" />
            </span>
          ) : null}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-blue-50/40 border-l-4 border-blue-300">
          <td colSpan={9} className="pb-2 pt-0">
            <VideosList pipelineId={pipelineId} runId={run.id} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function DayGroupRow({
  group,
  pipelineId,
  onClearDay,
}: {
  group: DayGroup;
  pipelineId: number;
  onClearDay: ClearRunsFn;
}) {
  const [open, setOpen] = useState(!group.latestRun.finished_at); // auto-open if latest run still live
  const [expandedRun, setExpandedRun] = useState<number | null>(null);

  const groupLabel = groupStatusLabel(group);
  const finishedIds = group.runs.filter((r) => r.finished_at).map((r) => r.id);

  return (
    <Fragment>
      {/* Day header row */}
      <tr
        className="cursor-pointer bg-slate-100 hover:bg-slate-200 border-l-4 border-slate-400"
        onClick={() => setOpen((o) => !o)}
      >
        <td className="py-2 pl-3 pr-1 text-slate-500">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </td>
        <td className="py-2 px-2 font-semibold text-slate-700 text-sm" colSpan={2}>
          {group.date}
        </td>
        <td className="py-2 px-2 text-xs text-slate-500" colSpan={3}>
          <span>{group.runs.length} lần chạy</span>
          {group.totalUploaded > 0 && (
            <span className="ml-2 font-medium text-emerald-600">{group.totalUploaded} đã đăng</span>
          )}
        </td>
        <td className="py-2 px-2" colSpan={1}>
          <span className={`text-xs whitespace-nowrap ${groupLabel.cls}`}>{groupLabel.text}</span>
        </td>
        <td className="py-2 pl-2 pr-4 text-right" onClick={(e) => e.stopPropagation()}>
          {finishedIds.length > 0 && (
            <button
              className="text-xs text-red-400 hover:text-red-600 flex items-center gap-1 ml-auto"
              onClick={() => onClearDay(finishedIds)}
              title={`Xóa ${finishedIds.length} log ngày ${group.date}`}
            >
              <Trash2 size={11} /> Xóa
            </button>
          )}
        </td>
        <td className="py-2 px-1 w-5" />
      </tr>

      {/* Run rows for this day */}
      {open && (
        <Fragment>
          <tr className="bg-slate-50 border-l-4 border-slate-200">
            <th className="py-1 pl-8 pr-1 w-6" />
            <th className="text-left py-1 px-2 text-[10px] text-slate-400 font-medium uppercase tracking-wide">Thời gian</th>
            <th className="text-left py-1 px-2 text-[10px] text-slate-400 font-medium uppercase tracking-wide">Đã tải về</th>
            <th className="text-left py-1 px-2 text-[10px] text-slate-400 font-medium uppercase tracking-wide">Đã đăng</th>
            <th className="text-left py-1 px-2 text-[10px] text-slate-400 font-medium uppercase tracking-wide">Bỏ qua</th>
            <th className="text-left py-1 px-2 text-[10px] text-slate-400 font-medium uppercase tracking-wide">Thất bại</th>
            <th className="text-left py-1 px-2 text-[10px] text-slate-400 font-medium uppercase tracking-wide">Trạng thái</th>
            <th className="py-1 px-1 w-5" />
          </tr>
          {group.runs.map((r) => (
            <RunRow
              key={r.id}
              pipelineId={pipelineId}
              run={r}
              expanded={expandedRun === r.id}
              onToggle={() => setExpandedRun(expandedRun === r.id ? null : r.id)}
            />
          ))}
          {/* Bottom border to close the day group */}
          <tr className="border-l-4 border-slate-200">
            <td colSpan={9} className="h-1 bg-slate-50" />
          </tr>
        </Fragment>
      )}
    </Fragment>
  );
}

const hasRunning = (runs: PipelineRunItem[]) => runs.some((r) => !r.finished_at);

export default function PipelineRunsExpand({ pipelineId }: Props) {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["pipeline-runs", pipelineId],
    queryFn: () => Pipelines.listRuns(pipelineId),
    refetchInterval: (query) => {
      const runs = query.state.data;
      return runs && hasRunning(runs) ? 5000 : false;
    },
  });

  const clearMut = useMutation({
    mutationFn: (runIds: number[]) => Pipelines.clearRuns(pipelineId, runIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pipeline-runs", pipelineId] });
      toast.success("Đã xóa log");
    },
    onError: (e: any) => toast.error("Xóa log thất bại", { description: e.message }),
  });

  if (isLoading) return <div className="p-3 text-xs text-slate-400">Đang tải…</div>;
  if (isError) return <div className="p-3 text-xs text-red-400">Không thể tải. Kiểm tra kết nối.</div>;
  if (!data?.length) return <div className="p-3 text-xs text-slate-400">Chưa có lần chạy nào.</div>;

  const groups = groupByDay(data);

  return (
    <div className="bg-slate-50 border-t py-2">
      <table className="w-full text-xs table-fixed">
        <colgroup>
          <col className="w-6" />
          <col className="w-28" />
          <col className="w-16" />
          <col className="w-16" />
          <col className="w-16" />
          <col className="w-16" />
          <col className="w-20" />
          <col className="w-16" />
        </colgroup>
        <tbody>
          {groups.map((g) => (
            <DayGroupRow
              key={g.date}
              group={g}
              pipelineId={pipelineId}
              onClearDay={(runIds) => clearMut.mutate(runIds)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
