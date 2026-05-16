import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api, Accounts } from "../api/client";
import { Pipelines, VideoUploadItem } from "../api/pipelines";
import ConfirmDialog from "../components/ConfirmDialog";

function fetchVideos(params: Record<string, string | undefined>) {
  const cleaned: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v) cleaned[k] = v;
  }
  return api.get<VideoUploadItem[]>("/videos", { params: cleaned }).then((r) => r.data);
}

const statusBadge = (s: string) =>
  s === "succeeded"
    ? "bg-emerald-100 text-emerald-700"
    : "bg-red-100 text-red-700";

export default function VideosPage() {
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

  const { data, isLoading } = useQuery({
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
      setDeleteTarget(null);
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Upload History</h2>
        <p className="text-sm text-slate-500 mt-1">
          All video uploads tracked by pipeline runs.
        </p>
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <select
          className="input w-auto"
          value={accountFilter}
          onChange={(e) => setAccountFilter(e.target.value)}
        >
          <option value="">All accounts</option>
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
          <option value="">All status</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
        </select>
        <select
          className="input w-auto"
          value={pipelineFilter}
          onChange={(e) => setPipelineFilter(e.target.value)}
        >
          <option value="">All pipelines</option>
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
              <th className="text-left p-3">Time</th>
              <th className="text-left p-3">Account</th>
              <th className="text-left p-3">YouTube</th>
              <th className="text-left p-3">TikTok</th>
              <th className="text-left p-3">Caption</th>
              <th className="text-left p-3">Status</th>
              <th className="text-left p-3">Attempts</th>
              <th className="p-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr>
                <td className="p-4 text-slate-500" colSpan={8}>Loading…</td>
              </tr>
            )}
            {!isLoading && data && data.length === 0 && (
              <tr>
                <td className="p-6 text-center text-slate-500" colSpan={8}>
                  No upload history yet.
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
                    className="text-blue-600 hover:underline"
                    title={v.youtube_title || v.youtube_url}
                  >
                    {v.youtube_title || v.youtube_video_id}
                  </a>
                </td>
                <td className="p-3">
                  {v.tiktok_url ? (
                    <a href={v.tiktok_url} target="_blank" className="text-blue-600 hover:underline">
                      View
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
                    <Trash2 size={14} className="inline" /> Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete upload record?"
        body={
          <p>
            This removes the upload record from the database.
            The actual TikTok video is NOT affected.
          </p>
        }
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />
    </div>
  );
}
