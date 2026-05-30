import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, Accounts } from "../api/client";
import { Pipelines, VideoUploadItem } from "../api/pipelines";
import ConfirmDialog from "../components/ConfirmDialog";
import { statusBadge } from "../utils/statusBadge";

function fetchVideos(params: Record<string, string | undefined>) {
  const cleaned: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v) cleaned[k] = v;
  }
  return api.get<VideoUploadItem[]>("/videos", { params: cleaned }).then((r) => r.data);
}

export default function HistoriesPage() {
  const qc = useQueryClient();

  const [accountFilter, setAccountFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [pipelineFilter, setPipelineFilter] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<VideoUploadItem | null>(null);

  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: Accounts.list,
  });
  const { data: pipelines } = useQuery({
    queryKey: ["pipelines"],
    queryFn: Pipelines.list,
  });

  const { data, isLoading, isError } = useQuery({
    queryKey: ["videos", accountFilter, statusFilter, pipelineFilter],
    queryFn: () =>
      fetchVideos({
        account_id: accountFilter || undefined,
        status: statusFilter || undefined,
        pipeline_id: pipelineFilter || undefined,
      }),
    refetchInterval: 10000,
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.delete(`/videos/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["videos"] });
      toast.success("Đã xóa bản ghi đăng tải");
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      toast.error("Xóa thất bại", { description: e?.response?.data?.detail || e.message });
      setDeleteTarget(null);
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Lịch sử</h2>
        <p className="text-sm text-slate-500 mt-1">
          Lịch sử đăng tải theo lịch và lần chạy.
        </p>
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <select
          className="input w-auto"
          value={accountFilter}
          onChange={(e) => setAccountFilter(e.target.value)}
        >
          <option value="">Tất cả tài khoản</option>
          {accounts?.map((a) => (
            <option key={a.id} value={a.id}>
              {a.display_name || a.username}
            </option>
          ))}
        </select>
        <select
          className="input w-auto"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">Tất cả trạng thái</option>
          <option value="succeeded">Thành công</option>
          <option value="failed">Thất bại</option>
        </select>
        <select
          className="input w-auto"
          value={pipelineFilter}
          onChange={(e) => setPipelineFilter(e.target.value)}
        >
          <option value="">Tất cả lịch đăng</option>
          {pipelines?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="text-left p-3">Thời gian</th>
              <th className="text-left p-3">Tài khoản</th>
              <th className="text-left p-3">YouTube</th>
              <th className="text-left p-3">TikTok</th>
              <th className="text-left p-3">Caption</th>
              <th className="text-left p-3">Trạng thái</th>
              <th className="text-left p-3">Số lần thử</th>
              <th className="p-3 text-right">Thao tác</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr>
                <td className="p-4 text-slate-500" colSpan={8}>Đang tải…</td>
              </tr>
            )}
            {isError && (
              <tr>
                <td className="p-4 text-center text-red-400" colSpan={8}>Không thể tải. Kiểm tra kết nối.</td>
              </tr>
            )}
            {!isLoading && !isError && data && data.length === 0 && (
              <tr>
                <td className="p-6 text-center text-slate-500" colSpan={8}>
                  Chưa có lịch sử đăng tải nào.
                </td>
              </tr>
            )}
            {data?.map((v) => (
              <tr key={v.id}>
                <td className="p-3 text-xs text-slate-600">
                  {v.created_at ? new Date(v.created_at).toLocaleString() : "—"}
                </td>
                <td className="p-3 text-slate-600">{v.account_id}</td>
                <td className="p-3 max-w-[140px] truncate">
                  <a
                    href={v.youtube_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline"
                    title={v.youtube_title || v.youtube_url}
                  >
                    {v.youtube_title || v.youtube_video_id}
                  </a>
                </td>
                <td className="p-3">
                  {v.tiktok_url ? (
                    <a href={v.tiktok_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                      Xem
                    </a>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="p-3 max-w-[120px] truncate text-slate-500" title={v.rendered_caption || ""}>
                  {v.rendered_caption || "—"}
                </td>
                <td className="p-3">
                  <span className={"chip " + statusBadge(v.status)}>{v.status}</span>
                  {v.error_text && (
                    <div className="text-xs text-slate-400 mt-0.5 max-w-[120px] truncate" title={v.error_text}>
                      {v.error_text}
                    </div>
                  )}
                </td>
                <td className="p-3 text-slate-500">{v.attempt_count}</td>
                <td className="p-3 text-right">
                  <button
                    className="text-xs text-red-600"
                    onClick={() => setDeleteTarget(v)}
                  >
                    <Trash2 size={14} className="inline" /> Xóa
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Xóa bản ghi đăng tải?"
        body={
          <p>
            Bản ghi này sẽ bị xóa khỏi hệ thống.
            Video TikTok thực tế KHÔNG bị ảnh hưởng.
          </p>
        }
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />
    </div>
  );
}
