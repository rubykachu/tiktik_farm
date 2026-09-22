import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, Accounts } from "../api/client";
import { Pipelines, VideoUploadItem } from "../api/pipelines";
import ConfirmDialog from "../components/ConfirmDialog";
import { statusBadge } from "../utils/statusBadge";
import { formatDateTime } from "../utils/datetime";

const PAGE_SIZE = 50;

const STATUS_LABEL: Record<string, string> = {
  succeeded: "thành công",
  failed: "thất bại",
};

interface VideoPage {
  items: VideoUploadItem[];
  total: number;
  page: number;
  size: number;
}

function fetchVideos(params: Record<string, string | undefined>) {
  const cleaned: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v) cleaned[k] = v;
  }
  return api.get<VideoPage>("/videos", { params: cleaned }).then((r) => r.data);
}

export default function HistoriesPage() {
  const qc = useQueryClient();

  const [accountFilter, setAccountFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [pipelineFilter, setPipelineFilter] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [page, setPage] = useState(1);
  const [deleteTarget, setDeleteTarget] = useState<VideoUploadItem | null>(null);

  /** Any filter change invalidates the current page number: page 7 of the old
   *  result set is usually past the end of the new one. */
  function applyFilter(set: (v: string) => void) {
    return (value: string) => {
      set(value);
      setPage(1);
    };
  }

  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: Accounts.list,
  });
  const { data: pipelines } = useQuery({
    queryKey: ["pipelines", "all"],
    queryFn: Pipelines.listAll,
  });

  const { data, isLoading, isError } = useQuery({
    queryKey: [
      "videos", accountFilter, statusFilter, pipelineFilter, fromDate, toDate, page,
    ],
    queryFn: () =>
      fetchVideos({
        account_id: accountFilter || undefined,
        status: statusFilter || undefined,
        pipeline_id: pipelineFilter || undefined,
        // The API compares against a timestamp, so an inclusive end-of-day
        // needs the time appended — a bare date would drop that day's rows.
        from_date: fromDate ? `${fromDate}T00:00:00` : undefined,
        to_date: toDate ? `${toDate}T23:59:59` : undefined,
        page: String(page),
        size: String(PAGE_SIZE),
      }),
    refetchInterval: 10000,
    placeholderData: (prev) => prev,
  });

  const rows = data?.items ?? [];
  const total = data?.total ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const accountName = (id: number) => {
    const a = accounts?.find((x) => x.id === id);
    return a ? a.display_name || a.username : `#${id}`;
  };

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
          onChange={(e) => applyFilter(setAccountFilter)(e.target.value)}
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
          onChange={(e) => applyFilter(setStatusFilter)(e.target.value)}
        >
          <option value="">Tất cả trạng thái</option>
          <option value="succeeded">Thành công</option>
          <option value="failed">Thất bại</option>
        </select>
        <select
          className="input w-auto"
          value={pipelineFilter}
          onChange={(e) => applyFilter(setPipelineFilter)(e.target.value)}
        >
          <option value="">Tất cả lịch đăng</option>
          {pipelines?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <div className="flex items-center gap-2">
          <input
            type="date"
            className="input w-auto"
            value={fromDate}
            max={toDate || undefined}
            onChange={(e) => applyFilter(setFromDate)(e.target.value)}
            aria-label="Từ ngày"
          />
          <span className="text-slate-400 text-sm">đến</span>
          <input
            type="date"
            className="input w-auto"
            value={toDate}
            min={fromDate || undefined}
            onChange={(e) => applyFilter(setToDate)(e.target.value)}
            aria-label="Đến ngày"
          />
        </div>
        {(accountFilter || statusFilter || pipelineFilter || fromDate || toDate) && (
          <button
            className="btn-secondary text-sm"
            onClick={() => {
              setAccountFilter("");
              setStatusFilter("");
              setPipelineFilter("");
              setFromDate("");
              setToDate("");
              setPage(1);
            }}
          >
            Xóa bộ lọc
          </button>
        )}
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="text-left p-3">Thời gian</th>
              <th className="text-left p-3">Tài khoản</th>
              {/* Any source: the column holds a YouTube, TikTok or Douyin link
                  depending on the pipeline the row came from. */}
              <th className="text-left p-3">Video gốc</th>
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
            {!isLoading && !isError && rows.length === 0 && (
              <tr>
                <td className="p-6 text-center text-slate-500" colSpan={8}>
                  {total === 0 && !accountFilter && !statusFilter && !pipelineFilter
                    && !fromDate && !toDate
                    ? "Chưa có lịch sử đăng tải nào. Lịch sử xuất hiện sau lần đăng đầu tiên."
                    : "Không có bản ghi nào khớp bộ lọc."}
                </td>
              </tr>
            )}
            {rows.map((v) => (
              <tr key={v.id}>
                <td className="p-3 text-xs text-slate-600 whitespace-nowrap">
                  {formatDateTime(v.created_at)}
                </td>
                <td className="p-3 text-slate-600">{accountName(v.account_id)}</td>
                <td className="p-3 max-w-[140px] truncate">
                  <a
                    href={v.youtube_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline"
                    title={v.source_title || v.youtube_url}
                  >
                    {v.source_title || v.youtube_video_id}
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
                  <span className={"chip " + statusBadge(v.status)}>
                    {STATUS_LABEL[v.status] ?? v.status}
                  </span>
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

      {total > 0 && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-slate-500 tabular-nums">
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} trên{" "}
            {total.toLocaleString("vi-VN")} bản ghi
          </span>
          <div className="flex items-center gap-2">
            <button
              className="btn-secondary px-2 py-1 disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              aria-label="Trang trước"
            >
              <ChevronLeft size={16} />
            </button>
            <span className="text-slate-600 tabular-nums">
              Trang {page}/{lastPage}
            </span>
            <button
              className="btn-secondary px-2 py-1 disabled:opacity-40"
              disabled={page >= lastPage}
              onClick={() => setPage((p) => p + 1)}
              aria-label="Trang sau"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}

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
