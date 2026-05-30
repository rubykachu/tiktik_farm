import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Play as RunNow, Pencil, Trash2 } from "lucide-react";
import { Pipelines, Pipeline } from "../api/pipelines";
import { toast } from "sonner";
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
  const { data, isLoading, isError } = useQuery({
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

  return (
    <>
      {activeCount > 5 && (
        <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {activeCount} lịch đang chạy. Hệ thống có thể chậm lại.
          Nên tạm dừng bớt một số.
        </div>
      )}

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="text-left p-3">Tên</th>
              <th className="text-left p-3">Kênh</th>
              <th className="text-left p-3">Tài khoản</th>
              <th className="text-left p-3">Chu kỳ</th>
              <th className="text-left p-3">Lần chạy tới</th>
              <th className="text-left p-3">Trạng thái</th>
              <th className="p-3 text-right">Thao tác</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr>
                <td className="p-4 text-slate-500" colSpan={7}>
                  Đang tải…
                </td>
              </tr>
            )}
            {isError && (
              <tr>
                <td className="p-4 text-center text-red-400" colSpan={7}>
                  Không thể tải. Kiểm tra kết nối.
                </td>
              </tr>
            )}
            {!isLoading && !isError && data && data.length === 0 && (
              <tr>
                <td className="p-6 text-center text-slate-500" colSpan={7}>
                  Chưa có lịch đăng nào.
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
                              onClick={() => toggleState.mutate(p)}
                            >
                              <Pause size={14} className="inline" /> Tạm dừng
                            </button>
                          </>
                        );
                      }
                      return (
                        <>
                          <button
                            className="text-xs text-slate-600"
                            onClick={() => runNowMut.mutate(p.id)}
                            title="Chạy ngay lần này"
                          >
                            <RunNow size={14} className="inline" /> Chạy ngay
                          </button>
                          <button
                            className="text-xs text-slate-600"
                            onClick={() => toggleState.mutate(p)}
                          >
                            <Pause size={14} className="inline" /> Tạm dừng
                          </button>
                        </>
                      );
                    })()}
                    {onEdit && (
                      <button
                        className="text-xs text-brand-600"
                        onClick={() => onEdit(p)}
                      >
                        <Pencil size={14} className="inline" /> Sửa
                      </button>
                    )}
                    <button
                      className="text-xs text-red-600"
                      onClick={() => setDeleteTarget(p)}
                    >
                      <Trash2 size={14} className="inline" /> Xóa
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
