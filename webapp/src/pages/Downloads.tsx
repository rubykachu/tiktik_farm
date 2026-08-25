import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Downloads, type ManualDownload } from "../api/downloads";
import ConfirmDialog from "../components/ConfirmDialog";
import { asUtcDate } from "../utils/datetime";

// Rows still working are polled; a finished list stops polling.
const POLL_MS = 3000;

const STATUS_LABEL: Record<string, string> = {
  queued: "Đang chờ",
  downloading: "Đang tải",
  ready: "Sẵn sàng",
  failed: "Thất bại",
};

const STATUS_CLASS: Record<string, string> = {
  queued: "bg-slate-100 text-slate-600",
  downloading: "bg-blue-50 text-blue-700",
  ready: "bg-green-50 text-green-700",
  failed: "bg-red-50 text-red-700",
};

function formatSize(bytes: number | null) {
  if (!bytes) return "—";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

function expiresIn(iso: string) {
  const hours = (asUtcDate(iso).getTime() - Date.now()) / 3_600_000;
  if (hours <= 0) return "hết hạn";
  return hours >= 1 ? `còn ${Math.floor(hours)}h` : `còn ${Math.max(1, Math.round(hours * 60))} phút`;
}

export default function DownloadsPage() {
  const qc = useQueryClient();
  const [urlsText, setUrlsText] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ManualDownload | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["downloads"],
    queryFn: Downloads.list,
    refetchInterval: (query) => {
      const rows = query.state.data as ManualDownload[] | undefined;
      const working = rows?.some((r) => r.status === "queued" || r.status === "downloading");
      return working ? POLL_MS : false;
    },
  });

  const createMut = useMutation({
    mutationFn: (urls: string[]) => Downloads.create(urls),
    onSuccess: (rows) => {
      qc.invalidateQueries({ queryKey: ["downloads"] });
      setUrlsText("");
      toast.success(`Đã thêm ${rows.length} video vào hàng chờ`);
    },
    onError: (e: any) =>
      toast.error("Không thêm được", {
        description: e?.response?.data?.detail ?? e.message,
      }),
  });

  const captionMut = useMutation({
    mutationFn: (id: number) => Downloads.caption(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["downloads"] });
      toast.success("Đã tạo caption");
    },
    onError: (e: any) =>
      toast.error("Tạo caption thất bại", {
        description: e?.response?.data?.detail ?? e.message,
      }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => Downloads.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["downloads"] });
      toast.success("Đã xóa");
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      toast.error("Xóa thất bại", { description: e?.response?.data?.detail ?? e.message });
      setDeleteTarget(null);
    },
  });

  function submitUrls() {
    const urls = urlsText
      .split(/\s+/)
      .map((u) => u.trim())
      .filter(Boolean);
    if (urls.length === 0) {
      toast.error("Chưa có URL nào");
      return;
    }
    createMut.mutate(urls);
  }

  const rows = data ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Tải video</h2>
        <p className="text-sm text-slate-500 mt-1">
          Dán link YouTube / TikTok / Douyin để tải video về và tự đăng lên TikTok.
          File tự xóa sau 24 giờ.
        </p>
      </div>

      <div className="space-y-2">
        <label className="label-required">URL video</label>
        <textarea
          className="input min-h-[90px] font-mono text-xs"
          value={urlsText}
          onChange={(e) => setUrlsText(e.target.value)}
          placeholder={"https://www.youtube.com/watch?v=...\nhttps://www.tiktok.com/@user/video/..."}
        />
        <div className="flex items-center gap-3">
          <button
            className="btn-primary"
            disabled={createMut.isPending}
            onClick={submitUrls}
          >
            {createMut.isPending ? "Đang gửi…" : "Tải video"}
          </button>
          <span className="text-xs text-slate-500">
            Mỗi dòng một link, tối đa 5 link mỗi lần.
          </span>
        </div>
      </div>

      {isLoading && <p className="text-sm text-slate-500">Đang tải…</p>}
      {isError && <p className="text-sm text-red-600">Không đọc được danh sách.</p>}

      {!isLoading && rows.length === 0 && (
        <p className="text-sm text-slate-500">Chưa có video nào.</p>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto border rounded">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="text-left p-2">Video</th>
                <th className="text-left p-2 w-28">Trạng thái</th>
                <th className="text-left p-2 w-24">Dung lượng</th>
                <th className="text-left p-2 w-28">Hết hạn</th>
                <th className="text-right p-2 w-40">Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t align-top">
                  <td className="p-2">
                    <div className="font-medium">
                      {r.title || "—"}
                      {r.origin === "pipeline" && (
                        <span className="ml-2 text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600 font-normal">
                          từ lịch đăng
                        </span>
                      )}
                    </div>
                    <a
                      className="text-xs text-brand-600 hover:underline break-all"
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {r.url}
                    </a>
                    {r.error_text && (
                      <p className="text-xs text-red-600 mt-1">{r.error_text}</p>
                    )}
                    {r.ai_caption && (
                      <div className="mt-2 flex items-start gap-1 bg-slate-50 rounded p-2">
                        <p className="text-xs whitespace-pre-wrap flex-1">{r.ai_caption}</p>
                        <button
                          className="text-slate-400 hover:text-slate-600"
                          title="Sao chép caption"
                          onClick={() => {
                            navigator.clipboard.writeText(r.ai_caption!);
                            toast.success("Đã sao chép");
                          }}
                        >
                          <Copy size={12} />
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="p-2">
                    <span
                      className={`text-xs px-2 py-1 rounded ${STATUS_CLASS[r.status] ?? ""}`}
                    >
                      {STATUS_LABEL[r.status] ?? r.status}
                    </span>
                  </td>
                  <td className="p-2 text-slate-600">{formatSize(r.file_size)}</td>
                  <td className="p-2 text-slate-600">{expiresIn(r.expires_at)}</td>
                  <td className="p-2">
                    <div className="flex items-center justify-end gap-2">
                      <a
                        className={`btn-secondary px-2 py-1 text-xs inline-flex items-center gap-1 ${
                          r.status === "ready" ? "" : "pointer-events-none opacity-40"
                        }`}
                        href={Downloads.fileUrl(r.id)}
                        title="Tải file về máy"
                      >
                        <Download size={14} /> Tải
                      </a>
                      <button
                        className="btn-secondary px-2 py-1 text-xs inline-flex items-center gap-1"
                        disabled={r.status !== "ready" || captionMut.isPending}
                        onClick={() => captionMut.mutate(r.id)}
                        title="Tạo caption bằng AI"
                      >
                        <Sparkles size={14} /> Caption
                      </button>
                      <button
                        className="text-slate-400 hover:text-red-600"
                        onClick={() => setDeleteTarget(r)}
                        title="Xóa"
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        title="Xóa video đã tải?"
        body={`Xóa "${deleteTarget?.title || deleteTarget?.url || ""}" khỏi máy chủ.`}
        confirmLabel="Xóa"
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
