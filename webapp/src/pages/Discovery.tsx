import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpDown, ExternalLink, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Discovery, type DiscoveryCandidate } from "../api/discovery";
import type { Pipeline } from "../api/pipelines";
import PipelineFormModal from "../components/PipelineFormModal";
import { formatRelative } from "../utils/datetime";

const SCAN_POLL_MS = 4000;
// How long to keep polling eagerly after "Quét ngay" before falling back to
// idle — the scheduler picks the request up on its next poll (≤30s default).
const EAGER_POLL_TIMEOUT_MS = 60_000;

// One dropdown instead of separate gl/hl inputs — hl is derived, not chosen.
const MARKETS = [
  { label: "Mỹ (English)", gl: "US", hl: "en" },
  { label: "Việt Nam (Tiếng Việt)", gl: "VN", hl: "vi" },
  { label: "Nhật Bản (日本語)", gl: "JP", hl: "ja" },
  { label: "Hàn Quốc (한국어)", gl: "KR", hl: "ko" },
];

type SortKey = "score" | "subscriber_count" | "median_view_count" | "days_since_last_video" | "cadence_days_per_video";

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}tr`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatDaysAgo(days: number): string {
  if (days < 1) return "hôm nay";
  return `${Math.round(days)} ngày trước`;
}

function formatCadence(days: number | null, tab: string | null): string {
  if (days == null) return "—";
  const tabLabel = tab === "shorts" ? "Shorts" : "kênh chính";
  return `${days.toFixed(1)} ngày/video (${tabLabel})`;
}

export default function DiscoveryPage() {
  const qc = useQueryClient();
  const [seed, setSeed] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [addTarget, setAddTarget] = useState<DiscoveryCandidate | null>(null);
  const [justTriggered, setJustTriggered] = useState(false);

  const { data: settings } = useQuery({ queryKey: ["discovery-settings"], queryFn: Discovery.getSettings });
  const { data: keywords } = useQuery({ queryKey: ["discovery-keywords"], queryFn: Discovery.listKeywords });
  const { data: candidates, isLoading: candidatesLoading } = useQuery({
    queryKey: ["discovery-candidates"],
    queryFn: Discovery.listCandidates,
  });
  const { data: scanRun } = useQuery({
    queryKey: ["discovery-scan-status"],
    queryFn: Discovery.scanStatus,
    refetchInterval: (query) => (query.state.data?.status === "running" || justTriggered ? SCAN_POLL_MS : false),
  });

  // Stop the eager post-trigger poll once the scheduler actually picks the
  // request up (status flips to "running") — the normal running-poll takes over.
  useEffect(() => {
    if (justTriggered && scanRun?.status === "running") setJustTriggered(false);
  }, [justTriggered, scanRun?.status]);

  useEffect(() => {
    if (!justTriggered) return;
    const timer = setTimeout(() => setJustTriggered(false), EAGER_POLL_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [justTriggered]);

  const scanNowMut = useMutation({
    mutationFn: Discovery.scanNow,
    onSuccess: () => {
      setJustTriggered(true);
      toast.success("Đã xếp lịch quét ngay — sẽ chạy trong ít phút");
    },
    onError: (e: any) =>
      toast.error("Không quét được", { description: e?.response?.data?.detail ?? e.message }),
  });

  const suggestMut = useMutation({
    mutationFn: (s: string) => Discovery.suggestKeywords(s),
    onSuccess: (list) => setSuggestions(list),
    onError: () => toast.error("Không lấy được gợi ý"),
  });

  const addKeywordMut = useMutation({
    mutationFn: (keyword: string) => Discovery.addKeyword(keyword),
    onSuccess: (_row, keyword) => {
      qc.invalidateQueries({ queryKey: ["discovery-keywords"] });
      setSuggestions((prev) => prev.filter((s) => s !== keyword));
      toast.success(`Đã thêm từ khóa "${keyword}"`);
    },
    onError: (e: any) =>
      toast.error("Không thêm được từ khóa", { description: e?.response?.data?.detail ?? e.message }),
  });

  const removeKeywordMut = useMutation({
    mutationFn: (id: number) => Discovery.removeKeyword(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery-keywords"] }),
  });

  const patchSettingsMut = useMutation({
    mutationFn: Discovery.patchSettings,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discovery-settings"] });
      toast.success("Đã lưu cài đặt");
    },
    onError: (e: any) => toast.error("Lưu cài đặt thất bại", { description: e?.response?.data?.detail ?? e.message }),
  });

  const skipMut = useMutation({
    mutationFn: (id: number) => Discovery.skipCandidate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discovery-candidates"] });
      toast.success("Đã bỏ qua kênh");
    },
  });

  const markAddedMut = useMutation({
    mutationFn: ({ candidateId, pipelineId }: { candidateId: number; pipelineId: number }) =>
      Discovery.markCandidateAdded(candidateId, pipelineId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery-candidates"] }),
  });

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const sortedCandidates = useMemo(() => {
    const rows = [...(candidates ?? [])];
    rows.sort((a, b) => {
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      return sortDir === "desc" ? bv - av : av - bv;
    });
    return rows;
  }, [candidates, sortKey, sortDir]);

  function SortHeader({ label, k }: { label: string; k: SortKey }) {
    return (
      <button
        className={`flex items-center gap-1 hover:text-slate-900 ${sortKey === k ? "text-slate-900 font-semibold" : ""}`}
        onClick={() => toggleSort(k)}
      >
        {label}
        <ArrowUpDown size={12} />
      </button>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Tìm kênh</h2>
        <p className="text-sm text-slate-500 mt-1">
          Quét YouTube theo từ khóa, xếp hạng kênh theo độ đều đặn đăng bài. Chỉ gợi ý —
          bạn tự quyết định thêm kênh nào vào lịch đăng.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <button
          className="btn-primary text-sm"
          disabled={scanNowMut.isPending || scanRun?.status === "running" || (keywords?.length ?? 0) === 0}
          onClick={() => scanNowMut.mutate()}
          title={(keywords?.length ?? 0) === 0 ? "Duyệt ít nhất 1 từ khóa trước" : undefined}
        >
          Quét ngay
        </button>
        <span className="text-xs text-slate-500">
          Tự động {settings?.scans_per_day ?? "…"} lần/ngày (tối thiểu cách nhau 4 giờ).
          {scanRun && ` Lần quét gần nhất: bắt đầu ${formatRelative(scanRun.started_at)}.`}
        </span>
      </div>

      {scanRun && (
        <div
          className={`rounded border p-3 text-sm ${
            scanRun.status === "running"
              ? "bg-blue-50 border-blue-200 text-blue-800"
              : scanRun.status === "error"
                ? "bg-red-50 border-red-200 text-red-700"
                : "bg-slate-50 border-slate-200 text-slate-600"
          }`}
        >
          {scanRun.status === "running" && (
            <>Đang quét: {scanRun.channels_done}/{scanRun.channels_total} kênh — từ khóa: {scanRun.keywords.join(", ")}</>
          )}
          {scanRun.status === "done" && (
            <>Lần quét gần nhất tìm được {scanRun.candidates_found} ứng viên — từ khóa: {scanRun.keywords.join(", ")}</>
          )}
          {scanRun.status === "error" && <>Lần quét gần nhất lỗi: {scanRun.error_text}</>}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-3 border rounded p-4">
          <h3 className="font-medium text-sm">Từ khóa quét</h3>
          <div className="flex gap-2">
            <input
              className="input flex-1"
              placeholder="Từ khóa gốc, vd: nhảy thiếu nhi"
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && seed.trim() && suggestMut.mutate(seed.trim())}
            />
            <button
              className="btn-secondary text-sm"
              disabled={!seed.trim() || suggestMut.isPending}
              onClick={() => suggestMut.mutate(seed.trim())}
            >
              Gợi ý
            </button>
          </div>

          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-slate-100 hover:bg-brand-50 hover:text-brand-700"
                  onClick={() => addKeywordMut.mutate(s)}
                  title="Thêm vào danh sách quét"
                >
                  <Plus size={12} /> {s}
                </button>
              ))}
            </div>
          )}

          <div className="space-y-1 pt-2 border-t">
            <p className="text-xs text-slate-500">Đã duyệt để quét tự động:</p>
            {(keywords?.length ?? 0) === 0 && <p className="text-sm text-slate-400">Chưa có từ khóa nào.</p>}
            <div className="flex flex-wrap gap-2">
              {keywords?.map((k) => (
                <span key={k.id} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-brand-50 text-brand-700">
                  {k.keyword}
                  <button onClick={() => removeKeywordMut.mutate(k.id)} title="Bỏ khỏi danh sách quét">
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        </div>

        <div className="space-y-3 border rounded p-4">
          <h3 className="font-medium text-sm">Cài đặt quét</h3>
          {settings && (
            <div className="grid grid-cols-2 gap-3 text-sm">
              <label className="space-y-1 col-span-2">
                <span className="text-xs text-slate-500">Thị trường</span>
                <select
                  className="input"
                  value={settings.market_gl}
                  onChange={(e) => {
                    const market = MARKETS.find((m) => m.gl === e.target.value);
                    if (market) patchSettingsMut.mutate({ market_gl: market.gl, market_hl: market.hl });
                  }}
                >
                  {MARKETS.map((m) => (
                    <option key={m.gl} value={m.gl}>
                      {m.label}
                    </option>
                  ))}
                  {!MARKETS.some((m) => m.gl === settings.market_gl) && (
                    <option value={settings.market_gl}>
                      {settings.market_gl}/{settings.market_hl} (tùy chỉnh)
                    </option>
                  )}
                </select>
              </label>
              <label className="space-y-1 col-span-2">
                <span className="text-xs text-slate-500">Nhịp đăng "đều đặn" (ngày/video)</span>
                <input
                  type="number"
                  min={0.1}
                  step={0.1}
                  className="input"
                  defaultValue={settings.target_days_per_video}
                  onBlur={(e) => {
                    const v = Number(e.target.value);
                    if (v > 0 && v !== settings.target_days_per_video) patchSettingsMut.mutate({ target_days_per_video: v });
                  }}
                />
              </label>
              <label className="space-y-1">
                <span className="text-xs text-slate-500">Sub tối thiểu</span>
                <input
                  type="number"
                  min={0}
                  className="input"
                  defaultValue={settings.min_subscribers}
                  onBlur={(e) => {
                    const v = Number(e.target.value);
                    if (v >= 0 && v !== settings.min_subscribers) patchSettingsMut.mutate({ min_subscribers: v });
                  }}
                />
              </label>
              <label className="space-y-1">
                <span className="text-xs text-slate-500">Lượt quét/ngày</span>
                <input
                  type="number"
                  min={1}
                  className="input"
                  defaultValue={settings.scans_per_day}
                  onBlur={(e) => {
                    const v = Number(e.target.value);
                    if (v >= 1 && v !== settings.scans_per_day) patchSettingsMut.mutate({ scans_per_day: v });
                  }}
                />
              </label>
            </div>
          )}
        </div>
      </div>

      {candidatesLoading && <p className="text-sm text-slate-500">Đang tải…</p>}
      {!candidatesLoading && (candidates?.length ?? 0) === 0 && (
        <p className="text-sm text-slate-500">Chưa có ứng viên nào. Duyệt từ khóa rồi chờ lượt quét tiếp theo.</p>
      )}

      {(candidates?.length ?? 0) > 0 && (
        <div className="overflow-x-auto border rounded">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="text-left p-2">Kênh</th>
                <th className="text-left p-2 w-20"><SortHeader label="Điểm" k="score" /></th>
                <th className="text-left p-2 w-20"><SortHeader label="Sub" k="subscriber_count" /></th>
                <th className="text-left p-2 w-24"><SortHeader label="View" k="median_view_count" /></th>
                <th className="text-left p-2 w-28"><SortHeader label="Mới nhất" k="days_since_last_video" /></th>
                <th className="text-left p-2 w-40"><SortHeader label="Nhịp đăng" k="cadence_days_per_video" /></th>
                <th className="text-left p-2 w-24">Shorts</th>
                <th className="text-left p-2 w-28">Từ khóa</th>
                <th className="text-right p-2 w-56">Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {sortedCandidates.map((c) => (
                <tr key={c.id} className="border-t align-top">
                  <td className="p-2 font-medium max-w-[220px] truncate" title={c.channel_title}>
                    {c.channel_title}
                  </td>
                  <td className="p-2 font-semibold">{c.score.toFixed(0)}</td>
                  <td className="p-2 text-slate-600">{formatCount(c.subscriber_count)}</td>
                  <td className="p-2 text-slate-600">{formatCount(c.median_view_count)}</td>
                  <td className="p-2 text-slate-600">{formatDaysAgo(c.days_since_last_video)}</td>
                  <td className="p-2 text-slate-600">{formatCadence(c.cadence_days_per_video, c.cadence_tab)}</td>
                  <td className="p-2 text-slate-600">
                    {c.has_shorts_tab ? `Có (${c.recent_shorts_count})` : "Không"}
                  </td>
                  <td className="p-2 text-slate-500 max-w-[100px] truncate" title={c.found_via_keyword}>
                    {c.found_via_keyword}
                  </td>
                  <td className="p-2">
                    <div className="flex items-center justify-end gap-2">
                      <button className="btn-primary px-2 py-1 text-xs" onClick={() => setAddTarget(c)}>
                        Thêm vào lịch
                      </button>
                      <button
                        className="btn-secondary px-2 py-1 text-xs"
                        disabled={skipMut.isPending}
                        onClick={() => skipMut.mutate(c.id)}
                      >
                        Bỏ qua
                      </button>
                      <a
                        className="text-slate-400 hover:text-slate-600"
                        href={c.channel_url}
                        target="_blank"
                        rel="noreferrer"
                        title="Xem trên YouTube"
                      >
                        <ExternalLink size={16} />
                      </a>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <PipelineFormModal
        open={!!addTarget}
        pipeline={null}
        prefillChannelUrl={addTarget?.channel_url}
        onClose={() => setAddTarget(null)}
        onCreated={(created: Pipeline) => {
          if (addTarget) markAddedMut.mutate({ candidateId: addTarget.id, pipelineId: created.id });
        }}
      />
    </div>
  );
}
