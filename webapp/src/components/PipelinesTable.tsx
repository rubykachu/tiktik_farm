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
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 409 && detail?.code === "PIPELINE_RUNNING") {
        alert("Pipeline đang chạy. Stop và đợi tick xong trước khi xóa.");
      } else {
        alert(`Xóa lỗi: ${e.message}`);
      }
      setDeleteTarget(null);
    },
  });

  const [runNowDisabled, setRunNowDisabled] = useState<Set<number>>(new Set());

  function startRunNowCooldown(id: number) {
    setRunNowDisabled((prev) => new Set(prev).add(id));
    setTimeout(() => {
      setRunNowDisabled((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }, 5000);
  }

  const runNowMut = useMutation({
    mutationFn: (id: number) => Pipelines.runNow(id),
    onMutate: (id) => {
      startRunNowCooldown(id);
    },
    onSuccess: () => {
      alert("Đã xếp lịch chạy ngay");
      qc.invalidateQueries({ queryKey: ["pipelines"] });
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      if (detail?.code === "PIPELINE_RUNNING") {
        alert("Pipeline đang chạy, đợi xong.");
      } else if (detail?.code === "RUN_NOW_QUEUED") {
        alert("Đã xếp Run now, chờ tick tới.");
      } else {
        alert(`Lỗi: ${e.message}`);
      }
    },
  });

  const activeCount = data?.filter((p) => p.state === "active").length ?? 0;

  return (
    <>
      {activeCount > 5 && (
        <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          Bạn đang chạy {activeCount} pipeline. Hiệu suất xử lý có thể chậm.
          Cân nhắc Stop bớt.
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
                  Chưa có pipeline.
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
                    <button
                      className="text-xs text-slate-600 disabled:opacity-40"
                      disabled={runNowDisabled.has(p.id)}
                      onClick={() => runNowMut.mutate(p.id)}
                      title="Run now"
                    >
                      <RunNow size={14} className="inline" /> Run now
                    </button>
                    <button
                      className="text-xs text-slate-600"
                      onClick={() => toggleState.mutate(p)}
                    >
                      {p.state === "active" ? (
                        <>
                          <Pause size={14} className="inline" /> Stop
                        </>
                      ) : (
                        <>
                          <Play size={14} className="inline" /> Resume
                        </>
                      )}
                    </button>
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
        title={`Xóa pipeline "${deleteTarget?.name ?? ""}"?`}
        body={
          <p>
            PipelineRun history bị xóa theo. VideoUpload (upload history) giữ
            lại. Nếu pipeline đang chạy, request sẽ trả 409 — phải Stop trước.
          </p>
        }
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />
    </>
  );
}
