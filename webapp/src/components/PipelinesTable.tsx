import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Play as RunNow, Pencil, Trash2 } from "lucide-react";
import { Pipelines, Pipeline } from "../api/pipelines";
import ConfirmDialog from "./ConfirmDialog";
import PipelineRunsExpand from "./PipelineRunsExpand";

type Props = {
  onEdit?: (pipeline: Pipeline) => void;
};

const stateClass = (s: string) =>
  s === "active"
    ? "bg-emerald-100 text-emerald-700"
    : "bg-amber-100 text-amber-700";

function intervalLabel(p: Pipeline): string {
  return `${p.interval_value} ${p.interval_unit}`;
}

function accountCount(p: Pipeline): number {
  try {
    return (JSON.parse(p.account_ids_json) as number[]).length;
  } catch {
    return 0;
  }
}

export default function PipelinesTable({ onEdit }: Props) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["pipelines"],
    queryFn: Pipelines.list,
    refetchInterval: 5000,
  });

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
      alert(`Pipeline "${deleteTarget?.name ?? ""}" and its upload history were deleted.`);
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 409 && detail?.code === "PIPELINE_RUNNING") {
        alert("Pipeline is running. Stop it and wait for the tick to finish before deleting.");
      } else {
        alert(`Delete failed: ${e.message}`);
      }
      setDeleteTarget(null);
    },
  });

  const runNowMut = useMutation({
    mutationFn: (id: number) => Pipelines.runNow(id),
    onSuccess: () => {
      alert("Queued: pipeline will run on the next tick.");
      qc.invalidateQueries({ queryKey: ["pipelines"] });
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      if (detail?.code === "PIPELINE_PAUSED") {
        alert(detail?.message ?? "Pipeline is paused — resume it first.");
      } else if (detail?.code === "PIPELINE_RUNNING") {
        alert("Pipeline is running. Wait for the tick to finish.");
      } else if (detail?.code === "RUN_NOW_QUEUED") {
        alert("Run-now already queued. Wait for the next tick.");
      } else {
        alert(`Error: ${e.message}`);
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

  return (
    <>
      {activeCount > 5 && (
        <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {activeCount} pipelines are running. Throughput may slow down.
          Consider pausing some.
        </div>
      )}

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="text-left p-3">Name</th>
              <th className="text-left p-3">Channel</th>
              <th className="text-left p-3">Accounts</th>
              <th className="text-left p-3">Interval</th>
              <th className="text-left p-3">Next fire</th>
              <th className="text-left p-3">State</th>
              <th className="p-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr>
                <td className="p-4 text-slate-500" colSpan={7}>
                  Loading…
                </td>
              </tr>
            )}
            {!isLoading && data && data.length === 0 && (
              <tr>
                <td className="p-6 text-center text-slate-500" colSpan={7}>
                  No pipelines yet.
                </td>
              </tr>
            )}
            {data?.flatMap((p) => {
              const rows = [
                <tr
                  key={p.id}
                  className="hover:bg-slate-50 cursor-pointer"
                  onClick={() => setExpandedId(expandedId === p.id ? null : p.id)}
                >
                  <td className="p-3 font-medium">{p.name}</td>
                  <td className="p-3 text-slate-600 max-w-xs truncate" title={p.channel_url}>
                    {p.channel_url}
                  </td>
                  <td className="p-3 text-slate-600">{accountCount(p)}</td>
                  <td className="p-3 text-slate-600">{intervalLabel(p)}</td>
                  <td className="p-3 text-slate-600 text-xs">
                    {new Date(p.next_fire_at).toLocaleString()}
                  </td>
                  <td className="p-3">
                    <span className={"chip " + stateClass(p.state)}>{p.state}</span>
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
                            <Play size={14} className="inline" /> Resume
                          </button>
                        );
                      }
                      if (locked) {
                        return (
                          <span className="text-xs text-slate-500" title="Worker is processing this tick">
                            Running…
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
                              title="Scheduler will run on the next tick"
                            >
                              Queued…
                            </button>
                            <button
                              className="text-xs text-slate-600"
                              onClick={() => toggleState.mutate(p)}
                            >
                              <Pause size={14} className="inline" /> Pause
                            </button>
                          </>
                        );
                      }
                      return (
                        <>
                          <button
                            className="text-xs text-slate-600"
                            onClick={() => runNowMut.mutate(p.id)}
                            title="Run one tick immediately"
                          >
                            <RunNow size={14} className="inline" /> Run now
                          </button>
                          <button
                            className="text-xs text-slate-600"
                            onClick={() => toggleState.mutate(p)}
                          >
                            <Pause size={14} className="inline" /> Pause
                          </button>
                        </>
                      );
                    })()}
                    {onEdit && (
                      <button
                        className="text-xs text-brand-600"
                        onClick={() => onEdit(p)}
                      >
                        <Pencil size={14} className="inline" /> Edit
                      </button>
                    )}
                    <button
                      className="text-xs text-red-600"
                      onClick={() => setDeleteTarget(p)}
                    >
                      <Trash2 size={14} className="inline" /> Delete
                    </button>
                  </td>
                </tr>,
              ];
              if (expandedId === p.id) {
                rows.push(
                  <tr key={`${p.id}-expand`}>
                    <td colSpan={7} className="p-0">
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
        title={`Delete pipeline "${deleteTarget?.name ?? ""}"?`}
        body={
          <p>
            Pipeline runs and upload history will be permanently deleted with
            the pipeline. This cannot be undone. If the pipeline is currently
            running, stop it first.
          </p>
        }
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />
    </>
  );
}
