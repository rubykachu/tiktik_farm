import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpDown, ExternalLink, HelpCircle, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Discovery, DISCOVERY_MAX_KEYWORDS_PER_SCAN, type DiscoveryScanResult } from "../api/discovery";
import type { Pipeline } from "../api/pipelines";
import PipelineFormModal, { type PipelinePrefill } from "../components/PipelineFormModal";
import { asUtcDate, formatRelative } from "../utils/datetime";

const SCAN_POLL_MS = 3000;

type SortKey = "score" | "subscriber_count" | "median_view_count" | "days_since_last_video" | "cadence_days_per_video";

const MARKETS = [
  { label: "Anh", gl: "GB", hl: "en" },
  { label: "Ấn Độ", gl: "IN", hl: "hi" },
  { label: "Brazil", gl: "BR", hl: "pt" },
  { label: "Hàn Quốc", gl: "KR", hl: "ko" },
  { label: "Indonesia", gl: "ID", hl: "id" },
  { label: "Mexico", gl: "MX", hl: "es" },
  { label: "Mỹ", gl: "US", hl: "en" },
  { label: "Nhật Bản", gl: "JP", hl: "ja" },
  { label: "Thái Lan", gl: "TH", hl: "th" },
  { label: "Trung Quốc", gl: "CN", hl: "zh-CN" },
  { label: "Việt Nam", gl: "VN", hl: "vi" },
];

const SCORE_HELP =
  "Điểm 0-100, ưu tiên độ đều đặn hơn quy mô kênh: độ mới video gần nhất (30đ) " +
  "+ nhịp đăng so với mục tiêu \"đều đặn\" trong Cài đặt (35đ) " +
  "+ view trung vị theo thang log (15đ) " +
  "+ có tab Shorts và số Shorts gần đây (20đ).";

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}tr`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatDaysAgo(days: number): string {
  if (!Number.isFinite(days)) return "—";
  if (days < 1) return "hôm nay";
  return `${Math.round(days)} ngày trước`;
}

function formatCadence(days: number | null, tab: string | null): string {
  if (days == null) return "—";
  const tabLabel = tab === "shorts" ? "Shorts" : "kênh chính";
  return `${days.toFixed(1)} ngày/video (${tabLabel})`;
}

function marketLabel(gl: string): string {
  return MARKETS.find((m) => m.gl === gl)?.label ?? gl;
}

function localHHMM(iso: string): string {
  const d = asUtcDate(iso);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

function addMinutesToHHMM(hhmm: string, minutes: number): string {
  const [h, m] = hhmm.split(":").map(Number);
  const total = (h * 60 + m + minutes) % (24 * 60);
  return `${Math.floor(total / 60).toString().padStart(2, "0")}:${(total % 60).toString().padStart(2, "0")}`;
}

function dayAfterLocalDate(iso: string): string {
  const d = asUtcDate(iso);
  const next = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
  return `${next.getFullYear()}-${(next.getMonth() + 1).toString().padStart(2, "0")}-${next
    .getDate()
    .toString()
    .padStart(2, "0")}`;
}

// window_delay_minutes has a hard floor of 3 in PipelineCreate validation —
// the user asked for 1 min spacing, but 3 is the closest the system allows
// (guards against a run still in flight when the next window tick fires).
const WINDOW_MINUTES = 15;
const WINDOW_DELAY_MINUTES = 3;

function buildPrefill(r: DiscoveryScanResult): PipelinePrefill {
  const prefill: PipelinePrefill = {
    channel_url: r.channel_url,
    name: r.channel_title,
    channel_description: r.channel_description ?? undefined,
    fetch_count: 1,
    interval_value: 1,
    interval_unit: "days",
    max_duration_seconds: r.median_duration_seconds ?? undefined,
  };
  if (r.latest_video_posted_at) {
    const firstFire = localHHMM(r.latest_video_posted_at);
    prefill.first_fire_at_local = firstFire;
    prefill.window_end_time_local = addMinutesToHHMM(firstFire, WINDOW_MINUTES);
    prefill.window_delay_minutes = WINDOW_DELAY_MINUTES;
    // Their newest video already posted today by the time we scan it — start
    // collecting from the day after so this pipeline doesn't already miss it.
    prefill.sync_from_date = dayAfterLocalDate(r.latest_video_posted_at);
  }
  return prefill;
}

export default function DiscoveryPage() {
  const qc = useQueryClient();
  const [seed, setSeed] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [market, setMarket] = useState(MARKETS.find((m) => m.gl === "US")!);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [addTarget, setAddTarget] = useState<DiscoveryScanResult | null>(null);
  const [viewingRunId, setViewingRunId] = useState<number | null>(null);

  const { data: settings } = useQuery({ queryKey: ["discovery-settings"], queryFn: Discovery.getSettings });
  const { data: keywords } = useQuery({ queryKey: ["discovery-keywords"], queryFn: Discovery.listKeywords });
  const { data: scanRuns } = useQuery({ queryKey: ["discovery-scan-runs"], queryFn: Discovery.listScanRuns });

  // Default to the most recent search on load, so the page isn't empty.
  useEffect(() => {
    if (viewingRunId == null && scanRuns && scanRuns.length > 0) setViewingRunId(scanRuns[0].id);
  }, [scanRuns, viewingRunId]);

  const { data: viewingRun } = useQuery({
    queryKey: ["discovery-scan-run", viewingRunId],
    queryFn: () => Discovery.getScanRun(viewingRunId!),
    enabled: viewingRunId != null,
    refetchInterval: (query) => (query.state.data?.status === "running" ? SCAN_POLL_MS : false),
  });

  const { data: results, isLoading: resultsLoading } = useQuery({
    queryKey: ["discovery-scan-results", viewingRunId],
    queryFn: () => Discovery.listScanResults(viewingRunId!),
    enabled: viewingRunId != null,
  });

  // Results only exist once the scan is done — refetch on the running -> done/error transition.
  const prevStatus = useMemo(() => viewingRun?.status, [viewingRun?.status]);
  useEffect(() => {
    if (prevStatus === "done" || prevStatus === "error") {
      qc.invalidateQueries({ queryKey: ["discovery-scan-results", viewingRunId] });
      qc.invalidateQueries({ queryKey: ["discovery-scan-runs"] });
    }
  }, [prevStatus, viewingRunId, qc]);

  const suggestMut = useMutation({
    mutationFn: (s: string) => Discovery.suggestKeywords(s, market.gl, market.hl),
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

  const scanNowMut = useMutation({
    mutationFn: () => Discovery.scanNow((keywords ?? []).map((k) => k.keyword), market.gl, market.hl),
    onSuccess: (res) => {
      setViewingRunId(res.scan_run_id);
      qc.invalidateQueries({ queryKey: ["discovery-scan-runs"] });
      qc.invalidateQueries({ queryKey: ["discovery-scan-run", res.scan_run_id] });
      toast.success("Đang quét…");
    },
    onError: (e: any) => toast.error("Không quét được", { description: e?.response?.data?.detail ?? e.message }),
  });

  const skipMut = useMutation({
    mutationFn: (r: DiscoveryScanResult) => Discovery.skipChannel(r.channel_id, r.channel_url, r.channel_title),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discovery-scan-results", viewingRunId] });
      toast.success("Đã bỏ qua kênh");
    },
  });

  const markAddedMut = useMutation({
    mutationFn: ({ r, pipelineId }: { r: DiscoveryScanResult; pipelineId: number }) =>
      Discovery.markChannelAdded(r.channel_id, r.channel_url, r.channel_title, pipelineId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery-scan-results", viewingRunId] }),
  });

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const visibleResults = useMemo(() => (results ?? []).filter((r) => r.status === "new"), [results]);
  const sortedResults = useMemo(() => {
    const rows = [...visibleResults];
    rows.sort((a, b) => {
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      return sortDir === "desc" ? bv - av : av - bv;
    });
    return rows;
  }, [visibleResults, sortKey, sortDir]);

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

  const keywordCount = keywords?.length ?? 0;
  const atCap = keywordCount >= DISCOVERY_MAX_KEYWORDS_PER_SCAN;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Tìm kênh</h2>
        <p className="text-sm text-slate-500 mt-1">
          Quét YouTube theo từ khóa, xếp hạng kênh theo độ đều đặn đăng bài. Chỉ gợi ý —
          bạn tự quyết định thêm kênh nào vào lịch đăng.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-3 border rounded p-4">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-sm">Tìm & quét</h3>
            <div className="flex items-center gap-1.5">
              <select
                className="input w-40 text-sm"
                value={market.gl}
                onChange={(e) => setMarket(MARKETS.find((m) => m.gl === e.target.value) ?? market)}
              >
                {MARKETS.map((m) => (
                  <option key={m.gl} value={m.gl}>
                    Thị trường: {m.label}
                  </option>
                ))}
              </select>
              <span title="Đã kiểm chứng thực tế: chỉ ảnh hưởng chắc chắn tới gợi ý từ khóa. Bước tìm kênh chính (ytsearch) không có cách đảm bảo lọc đúng theo thị trường/ngôn ngữ này.">
                <HelpCircle size={14} className="text-slate-400" />
              </span>
            </div>
          </div>

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
                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-slate-100 hover:bg-brand-50 hover:text-brand-700 disabled:opacity-40 disabled:cursor-not-allowed"
                  onClick={() => addKeywordMut.mutate(s)}
                  disabled={atCap}
                  title={atCap ? `Tối đa ${DISCOVERY_MAX_KEYWORDS_PER_SCAN} từ khóa/lần quét` : "Thêm vào lượt quét"}
                >
                  <Plus size={12} /> {s}
                </button>
              ))}
            </div>
          )}

          <div className="space-y-2 pt-2 border-t">
            <div className="flex items-center justify-between">
              <p className="text-xs text-slate-500">
                Từ khóa sẽ quét ({keywordCount}/{DISCOVERY_MAX_KEYWORDS_PER_SCAN}):
              </p>
              {atCap && <p className="text-xs text-amber-600">Đã đạt tối đa — bỏ 1 từ để thêm mới</p>}
            </div>
            {keywordCount === 0 && <p className="text-sm text-slate-400">Chưa có từ khóa nào.</p>}
            <div className="flex flex-wrap gap-2">
              {keywords?.map((k) => (
                <span key={k.id} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-brand-50 text-brand-700">
                  {k.keyword}
                  <button onClick={() => removeKeywordMut.mutate(k.id)} title="Bỏ khỏi lượt quét">
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
            <button
              className="btn-primary text-sm mt-2"
              disabled={scanNowMut.isPending || keywordCount === 0}
              onClick={() => scanNowMut.mutate()}
            >
              Quét ngay
            </button>
          </div>
        </div>

        <div className="space-y-3 border rounded p-4">
          <h3 className="font-medium text-sm">Ngưỡng chấm điểm</h3>
          {settings && (
            <div className="grid grid-cols-1 gap-3 text-sm">
              <label className="space-y-1">
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
                <span className="text-xs text-slate-500">Im lặng quá (tháng) thì loại</span>
                <input
                  type="number"
                  min={1}
                  className="input"
                  defaultValue={settings.silent_months}
                  onBlur={(e) => {
                    const v = Number(e.target.value);
                    if (v >= 1 && v !== settings.silent_months) patchSettingsMut.mutate({ silent_months: v });
                  }}
                />
              </label>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-3 space-y-3">
          {viewingRun && (
            <div
              className={`rounded border p-3 text-sm ${
                viewingRun.status === "running"
                  ? "bg-blue-50 border-blue-200 text-blue-800"
                  : viewingRun.status === "error"
                    ? "bg-red-50 border-red-200 text-red-700"
                    : "bg-slate-50 border-slate-200 text-slate-600"
              }`}
            >
              {viewingRun.status === "running" && (
                <div className="space-y-1">
                  <div>
                    Đang quét: {viewingRun.channels_done}/{viewingRun.channels_total} kênh — từ khóa:{" "}
                    {viewingRun.keywords.join(", ")} — thị trường: {marketLabel(viewingRun.market_gl)}
                  </div>
                  {viewingRun.channels_total > 0 && (
                    <div className="h-1.5 bg-blue-100 rounded overflow-hidden">
                      <div
                        className="h-full bg-blue-600 transition-all"
                        style={{ width: `${Math.round((viewingRun.channels_done / viewingRun.channels_total) * 100)}%` }}
                      />
                    </div>
                  )}
                </div>
              )}
              {viewingRun.status === "done" && (
                <>
                  Tìm được {viewingRun.candidates_found} ứng viên — từ khóa: {viewingRun.keywords.join(", ")} —
                  thị trường: {marketLabel(viewingRun.market_gl)}
                </>
              )}
              {viewingRun.status === "error" && <>Lỗi: {viewingRun.error_text}</>}
            </div>
          )}

          {resultsLoading && <p className="text-sm text-slate-500">Đang tải…</p>}
          {!resultsLoading && viewingRunId != null && sortedResults.length === 0 && viewingRun?.status !== "running" && (
            <p className="text-sm text-slate-500">Không có ứng viên mới cho lượt quét này.</p>
          )}
          {viewingRunId == null && <p className="text-sm text-slate-500">Chưa có lượt quét nào. Thêm từ khóa rồi bấm "Quét ngay".</p>}

          {sortedResults.length > 0 && (
            <div className="overflow-x-auto border rounded">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-slate-600">
                  <tr>
                    <th className="text-left p-2 whitespace-nowrap">Kênh</th>
                    <th className="text-left p-2 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1">
                        <SortHeader label="Điểm" k="score" />
                        <span title={SCORE_HELP}>
                          <HelpCircle size={12} className="text-slate-400" />
                        </span>
                      </span>
                    </th>
                    <th className="text-left p-2 whitespace-nowrap"><SortHeader label="Sub" k="subscriber_count" /></th>
                    <th className="text-left p-2 whitespace-nowrap"><SortHeader label="View" k="median_view_count" /></th>
                    <th className="text-left p-2 whitespace-nowrap"><SortHeader label="Mới nhất" k="days_since_last_video" /></th>
                    <th className="text-left p-2 whitespace-nowrap">Giờ đăng</th>
                    <th className="text-left p-2 whitespace-nowrap"><SortHeader label="Nhịp đăng" k="cadence_days_per_video" /></th>
                    <th className="text-left p-2 whitespace-nowrap">Shorts</th>
                    <th className="text-left p-2 whitespace-nowrap">Từ khóa</th>
                    <th className="text-right p-2 whitespace-nowrap">Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedResults.map((r) => (
                    <tr key={r.id} className="border-t align-top">
                      <td className="p-2 font-medium max-w-[220px] truncate" title={r.channel_description ?? r.channel_title}>
                        {r.channel_title}
                      </td>
                      <td className="p-2 font-semibold">{r.score.toFixed(0)}</td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">{formatCount(r.subscriber_count)}</td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">{formatCount(r.median_view_count)}</td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">{formatDaysAgo(r.days_since_last_video)}</td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">
                        {r.latest_video_posted_at ? localHHMM(r.latest_video_posted_at) : "—"}
                      </td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">{formatCadence(r.cadence_days_per_video, r.cadence_tab)}</td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">
                        {r.has_shorts_tab ? `Có (${r.recent_shorts_count})` : "Không"}
                      </td>
                      <td className="p-2 text-slate-500 max-w-[120px] truncate" title={r.found_via_keyword}>
                        {r.found_via_keyword}
                      </td>
                      <td className="p-2">
                        <div className="flex items-center justify-end gap-2">
                          <button className="btn-primary px-2 py-1 text-xs whitespace-nowrap" onClick={() => setAddTarget(r)}>
                            Thêm vào lịch
                          </button>
                          <button
                            className="btn-secondary px-2 py-1 text-xs whitespace-nowrap"
                            disabled={skipMut.isPending}
                            onClick={() => skipMut.mutate(r)}
                          >
                            Bỏ qua
                          </button>
                          <a
                            className="text-slate-400 hover:text-slate-600"
                            href={r.channel_url}
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
        </div>

        <div className="space-y-2 border rounded p-4">
          <h3 className="font-medium text-sm">Lịch sử quét</h3>
          {(scanRuns?.length ?? 0) === 0 && <p className="text-sm text-slate-400">Chưa quét lần nào.</p>}
          <div className="space-y-1 max-h-[500px] overflow-y-auto">
            {scanRuns?.map((run) => (
              <button
                key={run.id}
                onClick={() => setViewingRunId(run.id)}
                className={`w-full text-left text-xs p-2 rounded border ${
                  viewingRunId === run.id ? "border-brand-400 bg-brand-50" : "border-slate-200 hover:bg-slate-50"
                }`}
              >
                <div className="font-medium truncate">{run.keywords.join(", ")}</div>
                <div className="text-slate-500 flex items-center justify-between mt-0.5">
                  <span>
                    {formatRelative(run.started_at)} · {marketLabel(run.market_gl)}
                  </span>
                  <span>
                    {run.status === "running"
                      ? "đang quét…"
                      : run.status === "error"
                        ? "lỗi"
                        : `${run.candidates_found} kênh`}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      <PipelineFormModal
        open={!!addTarget}
        pipeline={null}
        prefill={addTarget ? buildPrefill(addTarget) : undefined}
        onClose={() => setAddTarget(null)}
        onCreated={(created: Pipeline) => {
          if (addTarget) markAddedMut.mutate({ r: addTarget, pipelineId: created.id });
        }}
      />
    </div>
  );
}
