import { Fragment, useMemo, useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Accounts } from "../api/client";
import { Pipelines, PipelineRunItem } from "../api/pipelines";
import { statusBadge } from "../utils/statusBadge";

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
  hasError: boolean;
  hasRunning: boolean;
}

function groupByDay(runs: PipelineRunItem[]): DayGroup[] {
  const map = new Map<string, PipelineRunItem[]>();
  for (const r of runs) {
    const date = r.started_at ? r.started_at.slice(0, 10) : "unknown";
    if (!map.has(date)) map.set(date, []);
    map.get(date)!.push(r);
  }
  return Array.from(map.entries()).map(([date, dayRuns]) => ({
    date,
    runs: dayRuns,
    totalUploaded: dayRuns.reduce((s, r) => s + r.uploaded_count, 0),
    hasError: dayRuns.some((r) => !!r.error_text),
    hasRunning: dayRuns.some((r) => !r.finished_at),
  }));
}

function groupStatusLabel(g: DayGroup): { text: string; cls: string } {
  if (g.hasRunning) return { text: "running", cls: "chip bg-blue-100 text-blue-700" };
  if (g.hasError) return { text: "has errors", cls: "chip bg-red-100 text-red-700" };
  if (g.totalUploaded === 0) return { text: "no uploads", cls: "text-slate-400 italic" };
  return { text: "done", cls: "chip bg-emerald-100 text-emerald-700" };
}

function runStatusLabel(r: PipelineRunItem): { text: string; cls: string } {
  if (!r.finished_at) {
    if (r.status_text) return { text: r.status_text, cls: "text-blue-700 italic" };
    return { text: "running", cls: "chip bg-blue-100 text-blue-700" };
  }
  if (r.error_text) return { text: "error", cls: "chip bg-red-100 text-red-700" };
  if (r.pulled_count === 0) return { text: "no new videos", cls: "text-slate-400 italic" };
  return { text: "done", cls: "chip bg-emerald-100 text-emerald-700" };
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

  if (isLoading) return <div className="p-2 text-xs text-slate-400">Loading videos…</div>;
  if (isError) return <div className="p-2 text-xs text-red-400">Failed to load videos.</div>;
  if (!data?.length) return <div className="p-2 text-xs text-slate-400">No videos.</div>;

  return (
    <>
      {errorModal && (
        <ErrorDetailModal text={errorModal} onClose={() => setErrorModal(null)} />
      )}
      <table className="w-full text-xs ml-8">
        <thead>
          <tr className="text-slate-400">
            <th className="text-left p-1">Account</th>
            <th className="text-left p-1">YouTube</th>
            <th className="text-left p-1">TikTok</th>
            <th className="text-left p-1">Status</th>
            <th className="text-left p-1">Error</th>
          </tr>
        </thead>
        <tbody>
          {data.map((v) => (
            <tr key={v.id} className="border-t border-slate-50">
              <td className="p-1">{accountLabel.get(v.account_id) ?? `#${v.account_id}`}</td>
              <td className="p-1 max-w-[120px] truncate">
                <a href={v.youtube_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                  {v.youtube_title || v.youtube_video_id}
                </a>
              </td>
              <td className="p-1">
                {v.tiktok_url ? (
                  <a href={v.tiktok_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                    {v.tiktok_video_id || "link"}
                  </a>
                ) : "—"}
              </td>
              <td className="p-1">
                <span className={"chip text-[10px] " + statusBadge(v.status)}>{v.status}</span>
              </td>
              <td className="p-1">
                {v.error_text ? (
                  <button
                    className="max-w-[150px] truncate text-red-600 hover:underline text-left"
                    onClick={() => setErrorModal(v.error_text || "")}
                  >
                    {v.error_text}
                  </button>
                ) : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
  const isError = status.text === "error";

  return (
    <Fragment>
      {errorModal && (
        <ErrorDetailModal text={errorModal} onClose={() => setErrorModal(null)} />
      )}
      <tr className="cursor-pointer hover:bg-slate-100" onClick={onToggle}>
        <td className="p-1 pl-6">
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </td>
        <td className="p-1 text-slate-500">
          {run.started_at ? new Date(run.started_at).toLocaleTimeString() : "—"}
        </td>
        <td className="p-1">{run.trigger_reason}</td>
        <td className="p-1">{run.pulled_count}</td>
        <td className="p-1">{run.uploaded_count}</td>
        <td className="p-1">{run.skipped_dup_count + run.skipped_exhausted_count}</td>
        <td className="p-1">{run.failed_count}</td>
        <td className="p-1 text-[10px]">
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
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8} className="p-0">
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
}: {
  group: DayGroup;
  pipelineId: number;
}) {
  const [open, setOpen] = useState(group.hasRunning); // auto-open if live
  const [expandedRun, setExpandedRun] = useState<number | null>(null);

  const groupLabel = groupStatusLabel(group);

  return (
    <Fragment>
      {/* Day header row */}
      <tr
        className="cursor-pointer bg-slate-100 hover:bg-slate-200 font-medium"
        onClick={() => setOpen((o) => !o)}
      >
        <td className="p-1">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </td>
        <td className="p-1 text-[11px]" colSpan={2}>
          {group.date}
        </td>
        <td className="p-1 text-[10px] text-slate-500" colSpan={3}>
          {group.runs.length} tick{group.runs.length !== 1 ? "s" : ""}
          {group.totalUploaded > 0 && (
            <span className="ml-2 text-emerald-700">{group.totalUploaded} uploaded</span>
          )}
        </td>
        <td className="p-1" colSpan={2}>
          <span className={`text-[10px] ${groupLabel.cls}`}>{groupLabel.text}</span>
        </td>
      </tr>

      {/* Run rows for this day */}
      {open && (
        <Fragment>
          <tr className="bg-slate-50">
            <th className="p-1 pl-6 w-6" />
            <th className="text-left p-1 text-[10px] text-slate-500 uppercase">Time</th>
            <th className="text-left p-1 text-[10px] text-slate-500 uppercase">Trigger</th>
            <th className="text-left p-1 text-[10px] text-slate-500 uppercase">Downloaded</th>
            <th className="text-left p-1 text-[10px] text-slate-500 uppercase">Uploaded</th>
            <th className="text-left p-1 text-[10px] text-slate-500 uppercase">Skipped</th>
            <th className="text-left p-1 text-[10px] text-slate-500 uppercase">Failed</th>
            <th className="text-left p-1 text-[10px] text-slate-500 uppercase">Status</th>
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
        </Fragment>
      )}
    </Fragment>
  );
}

const hasRunning = (runs: PipelineRunItem[]) => runs.some((r) => !r.finished_at);

export default function PipelineRunsExpand({ pipelineId }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["pipeline-runs", pipelineId],
    queryFn: () => Pipelines.listRuns(pipelineId),
    refetchInterval: (query) => {
      const runs = query.state.data;
      return runs && hasRunning(runs) ? 5000 : false;
    },
  });

  if (isLoading) return <div className="p-3 text-xs text-slate-400">Loading runs…</div>;
  if (isError) return <div className="p-3 text-xs text-red-400">Failed to load runs.</div>;
  if (!data?.length) return <div className="p-3 text-xs text-slate-400">No runs yet.</div>;

  const groups = groupByDay(data);

  return (
    <div className="bg-slate-50 border-t px-4 py-2">
      <table className="w-full text-xs">
        <tbody>
          {groups.map((g) => (
            <DayGroupRow key={g.date} group={g} pipelineId={pipelineId} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
