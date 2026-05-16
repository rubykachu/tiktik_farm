import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Pipelines, PipelineRunItem } from "../api/pipelines";
import { statusBadge } from "../utils/statusBadge";

type Props = { pipelineId: number };

function VideosList({ pipelineId, runId }: { pipelineId: number; runId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["run-videos", pipelineId, runId],
    queryFn: () => Pipelines.listRunVideos(pipelineId, runId),
  });

  if (isLoading) return <div className="p-2 text-xs text-slate-400">Loading videos…</div>;
  if (!data?.length) return <div className="p-2 text-xs text-slate-400">No videos.</div>;

  return (
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
            <td className="p-1">{v.account_id}</td>
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
              ) : (
                "—"
              )}
            </td>
            <td className="p-1">
              <span className={"chip text-[10px] " + statusBadge(v.status)}>{v.status}</span>
            </td>
            <td className="p-1 max-w-[150px] truncate text-slate-500" title={v.error_text || ""}>
              {v.error_text || ""}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const hasRunning = (runs: PipelineRunItem[]) => runs.some((r) => !r.finished_at);

export default function PipelineRunsExpand({ pipelineId }: Props) {
  const [expandedRun, setExpandedRun] = useState<number | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["pipeline-runs", pipelineId],
    queryFn: () => Pipelines.listRuns(pipelineId),
    refetchInterval: (query) => {
      const runs = query.state.data;
      return runs && hasRunning(runs) ? 5000 : false;
    },
  });

  if (isLoading) return <div className="p-3 text-xs text-slate-400">Loading runs…</div>;
  if (!data?.length) return <div className="p-3 text-xs text-slate-400">No runs yet.</div>;

  return (
    <div className="bg-slate-50 border-t px-4 py-2">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-500 uppercase text-[10px]">
            <th className="text-left p-1 w-6"></th>
            <th className="text-left p-1">Started</th>
            <th className="text-left p-1">Trigger</th>
            <th className="text-left p-1">Pulled</th>
            <th className="text-left p-1">Uploaded</th>
            <th className="text-left p-1">Failed</th>
            <th className="text-left p-1">Status</th>
          </tr>
        </thead>
        <tbody>
          {data.map((r) => (
            <Fragment key={r.id}>
              <tr
                className="cursor-pointer hover:bg-slate-100"
                onClick={() => setExpandedRun(expandedRun === r.id ? null : r.id)}
              >
                <td className="p-1">
                  {expandedRun === r.id ? (
                    <ChevronDown size={12} />
                  ) : (
                    <ChevronRight size={12} />
                  )}
                </td>
                <td className="p-1">
                  {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
                </td>
                <td className="p-1">{r.trigger_reason}</td>
                <td className="p-1">{r.pulled_count}</td>
                <td className="p-1">{r.uploaded_count}</td>
                <td className="p-1">{r.failed_count}</td>
                <td className="p-1">
                  {r.finished_at ? (
                    r.error_text ? (
                      <span className="chip bg-red-100 text-red-700 text-[10px]">error</span>
                    ) : (
                      <span className="chip bg-emerald-100 text-emerald-700 text-[10px]">done</span>
                    )
                  ) : (
                    <span className="chip bg-blue-100 text-blue-700 text-[10px]">running</span>
                  )}
                </td>
              </tr>
              {expandedRun === r.id && (
                <tr>
                  <td colSpan={7} className="p-0">
                    <VideosList pipelineId={pipelineId} runId={r.id} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
