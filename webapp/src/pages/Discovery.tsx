import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpDown, CalendarPlus, HelpCircle, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import {
  Discovery,
  DISCOVERY_MAX_KEYWORDS_PER_SCAN,
  type DiscoveryPlatform,
  type DiscoveryPlatformInfo,
  type DiscoveryScanResult,
  type DiscoveryScanRun,
} from "../api/discovery";
import type { Pipeline } from "../api/pipelines";
import ConfirmDialog from "../components/ConfirmDialog";
import PipelineFormModal, { type PipelinePrefill } from "../components/PipelineFormModal";
import Tooltip from "../components/Tooltip";
import { asUtcDate, formatRelative } from "../utils/datetime";

const SCAN_POLL_MS = 3000;

type SortKey =
  | "score"
  | "subscriber_count"
  | "median_view_count"
  | "days_since_last_video"
  | "cadence_videos_days_per_video"
  | "cadence_shorts_days_per_video";

const PLATFORM_LABELS: Record<DiscoveryPlatform, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  douyin: "Douyin",
};

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

const SCORE_HELP_TWO_FEEDS =
  "Điểm 0-100, ưu tiên nhịp đăng Shorts hơn cả: nhịp đăng Shorts so với mục tiêu " +
  "\"đều đặn\" trong Cài đặt (45đ) + nhịp đăng video dài (20đ) + độ mới video gần nhất (20đ) " +
  "+ view trung vị theo thang log, chỉ cộng thêm không trừ (15đ). Không có Shorts thì mất " +
  "45đ đó, không có video dài thì mất 20đ đó — không cộng bù qua nhau.";

const SCORE_HELP_ONE_FEED =
  "Điểm 0-100: nhịp đăng so với mục tiêu \"đều đặn\" trong Cài đặt (65đ) + độ mới video gần " +
  "nhất (20đ) + view trung vị theo thang log, chỉ cộng thêm không trừ (15đ). Nền tảng này chỉ " +
  "có một luồng đăng nên toàn bộ 65đ nhịp đăng dồn vào đó — điểm vẫn so sánh được với YouTube.";

const CADENCE_HELP_TWO_FEEDS =
  "Trung bình ngày/video, tính từ tối đa 12 video mới nhất của tab này (video dài và Shorts tính " +
  "riêng). Lấy ngày đăng thật của video mới nhất và video cũ nhất trong 12 video đó, chia khoảng " +
  "thời gian giữa hai video cho số khoảng cách giữa chúng, ví dụ đủ 12 video thì có 11 khoảng. " +
  "Chỉ phản ánh nhịp đăng gần đây, không tính toàn bộ lịch sử kênh.";

const CADENCE_HELP_ONE_FEED =
  "Trung bình ngày/video, tính từ tối đa 12 video mới nhất của kênh. Lấy ngày đăng thật của video " +
  "mới nhất và video cũ nhất trong 12 video đó, chia khoảng thời gian giữa hai video cho số khoảng " +
  "cách giữa chúng, ví dụ đủ 12 video thì có 11 khoảng. Chỉ phản ánh nhịp đăng gần đây, không tính " +
  "toàn bộ lịch sử kênh.";

// TikTok's channel candidates cannot become a pipeline: ChannelPipeline
// only pulls from YouTube and Douyin. The API says so per platform
// (pipeline_source_type), and the table drops its add buttons accordingly.
const NO_PIPELINE_HELP =
  "Lịch đăng chưa hỗ trợ lấy video từ nền tảng này, nên chỉ xem và bỏ qua được. " +
  "Muốn lấy video thì mở kênh rồi dùng màn hình Tải video.";

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

function formatCadence(days: number | null): string {
  if (days == null) return "—";
  return `${days.toFixed(1)}d/video`;
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

// On YouTube, which tab to pull from is the user's explicit choice (the add
// button lives in the Shorts/Video cadence column they clicked); sourceTab
// null means either the Thao tác fallback button, when neither tab had
// enough data to show a cadence-column add button at all, or a single-feed
// platform, which has no tab to pick.
function buildPrefill(
  r: DiscoveryScanResult,
  sourceTab: "shorts" | "videos" | null,
  sourceType: "youtube" | "tiktok" | "douyin",
): PipelinePrefill {
  const baseUrl = r.channel_url.replace(/\/+$/, "");
  const prefill: PipelinePrefill = {
    channel_url: sourceTab ? `${baseUrl}/${sourceTab}` : baseUrl,
    source_type: sourceType,
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
  const [platform, setPlatform] = useState<DiscoveryPlatform>("youtube");
  const [market, setMarket] = useState(MARKETS.find((m) => m.gl === "US")!);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [addTarget, setAddTarget] = useState<{ result: DiscoveryScanResult; sourceTab: "shorts" | "videos" | null } | null>(
    null
  );
  const [deleteTarget, setDeleteTarget] = useState<DiscoveryScanRun | null>(null);
  const [viewingRunId, setViewingRunId] = useState<number | null>(null);

  const { data: settings } = useQuery({ queryKey: ["discovery-settings"], queryFn: Discovery.getSettings });
  const { data: platforms } = useQuery({ queryKey: ["discovery-platforms"], queryFn: Discovery.listPlatforms });
  const platformInfo = (p: DiscoveryPlatform): DiscoveryPlatformInfo | undefined =>
    platforms?.find((x) => x.platform === p);
  // The platform being searched for, and the one the results on screen came
  // from — they differ the moment the picker is changed without re-scanning.
  const searchInfo = platformInfo(platform);
  // Purely client-side, cleared on every page load — an earlier version
  // persisted this batch across visits, but on a shared multi-user portal
  // that meant stale keywords from someone else's session sat there
  // unnoticed and silently combined with whatever the next person typed.
  const [keywords, setKeywords] = useState<string[]>([]);
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

  // Results, and every action on them, belong to the run being viewed —
  // not to whatever the picker above is currently set to.
  const viewingPlatform: DiscoveryPlatform = viewingRun?.platform ?? "youtube";
  const viewingInfo = platformInfo(viewingPlatform);

  const suggestMut = useMutation({
    mutationFn: (s: string) => Discovery.suggestKeywords(platform, s, market.gl, market.hl),
    onSuccess: (list) => setSuggestions(list),
    onError: () => toast.error("Không lấy được gợi ý"),
  });

  function addKeyword(keyword: string) {
    if (keywords.includes(keyword)) return;
    setKeywords((prev) => [...prev, keyword]);
    setSuggestions((prev) => prev.filter((s) => s !== keyword));
  }

  function removeKeyword(keyword: string) {
    setKeywords((prev) => prev.filter((k) => k !== keyword));
  }

  const patchSettingsMut = useMutation({
    mutationFn: Discovery.patchSettings,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discovery-settings"] });
      toast.success("Đã lưu cài đặt");
    },
    onError: (e: any) => toast.error("Lưu cài đặt thất bại", { description: e?.response?.data?.detail ?? e.message }),
  });

  const scanNowMut = useMutation({
    mutationFn: () => Discovery.scanNow(platform, keywords, market.gl, market.hl),
    onSuccess: (res) => {
      setViewingRunId(res.scan_run_id);
      qc.invalidateQueries({ queryKey: ["discovery-scan-runs"] });
      qc.invalidateQueries({ queryKey: ["discovery-scan-run", res.scan_run_id] });
      toast.success("Đang quét…");
    },
    onError: (e: any) => toast.error("Không quét được", { description: e?.response?.data?.detail ?? e.message }),
  });

  const skipMut = useMutation({
    mutationFn: (r: DiscoveryScanResult) =>
      Discovery.skipChannel(viewingPlatform, r.channel_id, r.channel_url, r.channel_title),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discovery-scan-results", viewingRunId] });
      toast.success("Đã bỏ qua kênh");
    },
  });

  const markAddedMut = useMutation({
    mutationFn: ({ r, pipelineId }: { r: DiscoveryScanResult; pipelineId: number }) =>
      Discovery.markChannelAdded(viewingPlatform, r.channel_id, r.channel_url, r.channel_title, pipelineId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery-scan-results", viewingRunId] }),
  });

  const deleteScanRunMut = useMutation({
    mutationFn: (id: number) => Discovery.deleteScanRun(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["discovery-scan-runs"] });
      if (viewingRunId === id) setViewingRunId(null);
      toast.success("Đã xoá lượt quét");
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      toast.error("Không xoá được", { description: e?.response?.data?.detail ?? e.message });
      setDeleteTarget(null);
    },
  });

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  // Skipped channels stay hidden — the user explicitly rejected them, so the
  // list should stay a clean working set. "added" channels stay visible
  // (badge instead of action buttons) so revisiting an old scan run doesn't
  // quietly lose the record that this channel was ever found and added,
  // e.g. after its pipeline gets deleted later.
  const singleFeed = viewingInfo?.single_feed ?? false;
  const canAddToPipeline = !!viewingInfo?.pipeline_source_type;

  const visibleResults = useMemo(() => (results ?? []).filter((r) => r.status !== "skipped"), [results]);
  const sortedResults = useMemo(() => {
    // The video-dài column is not rendered for a single-feed platform, so a
    // sort left over from a YouTube run would order every row by null there.
    const key =
      singleFeed && sortKey === "cadence_videos_days_per_video" ? "score" : sortKey;
    const rows = [...visibleResults];
    rows.sort((a, b) => {
      const av = a[key] ?? -Infinity;
      const bv = b[key] ?? -Infinity;
      return sortDir === "desc" ? bv - av : av - bv;
    });
    return rows;
  }, [visibleResults, sortKey, sortDir, singleFeed]);

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

  const keywordCount = keywords.length;
  const atCap = keywordCount >= DISCOVERY_MAX_KEYWORDS_PER_SCAN;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Tìm kênh</h2>
        <p className="text-sm text-slate-500 mt-1">
          Quét YouTube, TikTok hoặc Douyin theo từ khóa, xếp hạng kênh theo độ đều đặn đăng bài.
          Chỉ gợi ý — bạn tự quyết định thêm kênh nào vào lịch đăng.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-3 border rounded p-4">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-sm">Tìm & quét</h3>
            <div className="flex items-center gap-1.5">
              <select
                className="input w-36 text-sm"
                value={platform}
                onChange={(e) => {
                  setPlatform(e.target.value as DiscoveryPlatform);
                  setSuggestions([]);
                }}
              >
                {(platforms ?? []).map((p) => (
                  <option key={p.platform} value={p.platform}>
                    {PLATFORM_LABELS[p.platform]}
                  </option>
                ))}
              </select>
              {searchInfo?.market_aware && (
                <>
                  <select
                    className="input w-56 text-sm"
                    value={market.gl}
                    onChange={(e) => setMarket(MARKETS.find((m) => m.gl === e.target.value) ?? market)}
                  >
                    {MARKETS.map((m) => (
                      <option key={m.gl} value={m.gl}>
                        Thị trường: {m.label}
                      </option>
                    ))}
                  </select>
                  <Tooltip content="Chỉ chắc chắn đúng cho phần gợi ý từ khóa. Kết quả tìm kênh có thể không đúng thị trường này.">
                    <HelpCircle size={14} className="text-slate-400 cursor-help" />
                  </Tooltip>
                </>
              )}
            </div>
          </div>

          <div className="flex gap-2">
            <input
              className="input flex-1"
              placeholder="Từ khóa gốc, vd: nhảy thiếu nhi"
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
              onKeyDown={(e) => {
                if (e.key !== "Enter" || !seed.trim()) return;
                addKeyword(seed.trim());
                setSeed("");
              }}
            />
            <button
              className="btn-secondary text-sm whitespace-nowrap"
              disabled={!seed.trim() || atCap}
              title={atCap ? `Tối đa ${DISCOVERY_MAX_KEYWORDS_PER_SCAN} từ khóa/lần quét` : "Thêm thẳng từ khóa này"}
              onClick={() => {
                addKeyword(seed.trim());
                setSeed("");
              }}
            >
              Thêm
            </button>
            {searchInfo?.supports_suggestions && (
              <button
                className="btn-secondary text-sm"
                disabled={!seed.trim() || suggestMut.isPending}
                onClick={() => suggestMut.mutate(seed.trim())}
              >
                {suggestMut.isPending ? "Đang lấy…" : "Gợi ý"}
              </button>
            )}
          </div>
          {searchInfo && !searchInfo.supports_suggestions && (
            <p className="text-xs text-slate-500">
              {PLATFORM_LABELS[platform]} không có gợi ý từ khóa — gõ từ khóa rồi bấm "Thêm".
            </p>
          )}

          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-slate-100 hover:bg-brand-50 hover:text-brand-700 disabled:opacity-40 disabled:cursor-not-allowed"
                  onClick={() => addKeyword(s)}
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
              {keywords.map((k) => (
                <span key={k} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-brand-50 text-brand-700">
                  {k}
                  <button onClick={() => removeKeyword(k)} title="Bỏ khỏi lượt quét">
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
          <p className="text-xs text-slate-500">
            Điểm 0-100 = nhịp đăng Shorts so với "đều đặn" dưới đây (45đ) + nhịp đăng video dài (20đ)
            + độ mới video gần nhất (20đ) + view trung vị (15đ, chỉ cộng thêm không trừ). Thiếu Shorts
            mất 45đ, thiếu video dài mất 20đ — không cộng bù qua nhau.
          </p>
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
                    Đang quét {PLATFORM_LABELS[viewingRun.platform]}: {viewingRun.channels_done}/
                    {viewingRun.channels_total} kênh — từ khóa: {viewingRun.keywords.join(", ")}
                    {viewingInfo?.market_aware && <> — thị trường: {marketLabel(viewingRun.market_gl)}</>}
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
                  Tìm được {viewingRun.candidates_found} ứng viên trên {PLATFORM_LABELS[viewingRun.platform]} —
                  từ khóa: {viewingRun.keywords.join(", ")}
                  {viewingInfo?.market_aware && <> — thị trường: {marketLabel(viewingRun.market_gl)}</>}
                </>
              )}
              {viewingRun.status === "error" && <>Lỗi: {viewingRun.error_text}</>}
            </div>
          )}

          {viewingRun && viewingInfo && !canAddToPipeline && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
              {NO_PIPELINE_HELP}
            </p>
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
                    <th className="text-left p-2 whitespace-nowrap">#</th>
                    <th className="text-left p-2 whitespace-nowrap">Kênh</th>
                    <th className="text-left p-2 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1">
                        <SortHeader label="Điểm" k="score" />
                        <Tooltip content={singleFeed ? SCORE_HELP_ONE_FEED : SCORE_HELP_TWO_FEEDS} side="bottom">
                          <HelpCircle size={12} className="text-slate-400 cursor-help" />
                        </Tooltip>
                      </span>
                    </th>
                    <th className="text-left p-2 whitespace-nowrap">
                      <SortHeader label={singleFeed ? "Follower" : "Sub"} k="subscriber_count" />
                    </th>
                    <th className="text-left p-2 whitespace-nowrap"><SortHeader label="View" k="median_view_count" /></th>
                    <th className="text-left p-2 whitespace-nowrap"><SortHeader label="Last" k="days_since_last_video" /></th>
                    <th className="text-left p-2 whitespace-nowrap">Giờ đăng</th>
                    <th className="text-left p-2 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1">
                        <SortHeader
                          label={singleFeed ? "Nhịp đăng" : "Shorts"}
                          k="cadence_shorts_days_per_video"
                        />
                        <Tooltip
                          content={singleFeed ? CADENCE_HELP_ONE_FEED : CADENCE_HELP_TWO_FEEDS}
                          side="bottom"
                        >
                          <HelpCircle size={12} className="text-slate-400 cursor-help" />
                        </Tooltip>
                      </span>
                    </th>
                    {!singleFeed && (
                      <th className="text-left p-2 whitespace-nowrap">
                        <span className="inline-flex items-center gap-1">
                          <SortHeader label="Video" k="cadence_videos_days_per_video" />
                          <Tooltip content={CADENCE_HELP_TWO_FEEDS} side="bottom">
                            <HelpCircle size={12} className="text-slate-400 cursor-help" />
                          </Tooltip>
                        </span>
                      </th>
                    )}
                    <th className="text-left p-2 whitespace-nowrap">Từ khóa</th>
                    <th className="text-right p-2 whitespace-nowrap">Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedResults.map((r, idx) => (
                    <tr key={r.id} className="border-t align-top">
                      <td className="p-2 text-slate-400">{idx + 1}</td>
                      <td className="p-2 font-medium max-w-[220px]">
                        <Tooltip content={r.channel_description || r.channel_title} className="block">
                          <a
                            href={r.channel_url}
                            target="_blank"
                            rel="noreferrer"
                            className="block truncate hover:underline hover:text-brand-700"
                          >
                            {r.channel_title}
                          </a>
                        </Tooltip>
                      </td>
                      <td className="p-2 font-semibold">{r.score.toFixed(0)}</td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">{formatCount(r.subscriber_count)}</td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">{formatCount(r.median_view_count)}</td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">{formatDaysAgo(r.days_since_last_video)}</td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">
                        {r.latest_video_posted_at ? localHHMM(r.latest_video_posted_at) : "—"}
                      </td>
                      <td className="p-2 text-slate-600 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          <span>{formatCadence(r.cadence_shorts_days_per_video)}</span>
                          {canAddToPipeline && r.status !== "added" && r.cadence_shorts_days_per_video != null && (
                            <Tooltip content={singleFeed ? "Thêm vào lịch" : "Thêm vào lịch — nguồn: Shorts"}>
                              <button
                                className="text-brand-500 hover:text-brand-700"
                                onClick={() => setAddTarget({ result: r, sourceTab: singleFeed ? null : "shorts" })}
                              >
                                <CalendarPlus size={13} />
                              </button>
                            </Tooltip>
                          )}
                        </div>
                      </td>
                      {!singleFeed && (
                        <td className="p-2 text-slate-600 whitespace-nowrap">
                          <div className="flex items-center gap-1.5">
                            <span>{formatCadence(r.cadence_videos_days_per_video)}</span>
                            {canAddToPipeline && r.status !== "added" && r.cadence_videos_days_per_video != null && (
                              <Tooltip content="Thêm vào lịch — nguồn: video dài">
                                <button
                                  className="text-brand-500 hover:text-brand-700"
                                  onClick={() => setAddTarget({ result: r, sourceTab: "videos" })}
                                >
                                  <CalendarPlus size={13} />
                                </button>
                              </Tooltip>
                            )}
                          </div>
                        </td>
                      )}
                      <td className="p-2 text-slate-500 max-w-[120px]">
                        <Tooltip content={r.found_via_keyword} className="block truncate">
                          {r.found_via_keyword}
                        </Tooltip>
                      </td>
                      <td className="p-2">
                        {r.status === "added" ? (
                          <div className="flex items-center justify-end">
                            <span className="text-xs px-2 py-1 rounded bg-emerald-50 text-emerald-700 whitespace-nowrap">
                              Đã thêm
                            </span>
                          </div>
                        ) : (
                          <div className="flex items-center justify-end gap-2">
                            {canAddToPipeline &&
                              r.cadence_shorts_days_per_video == null &&
                              r.cadence_videos_days_per_video == null && (
                                <Tooltip content="Chưa rõ nhịp đăng ở tab nào — nhớ kiểm tra lại URL kênh trong form">
                                  <button
                                    className="btn-primary p-1.5"
                                    onClick={() => setAddTarget({ result: r, sourceTab: null })}
                                  >
                                    <CalendarPlus size={14} />
                                  </button>
                                </Tooltip>
                              )}
                            <button
                              className="btn-secondary px-2 py-1 text-xs whitespace-nowrap"
                              disabled={skipMut.isPending}
                              onClick={() => skipMut.mutate(r)}
                            >
                              Bỏ qua
                            </button>
                          </div>
                        )}
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
              <div
                key={run.id}
                className={`group flex items-start gap-1 w-full text-left text-xs p-2 rounded border ${
                  viewingRunId === run.id ? "border-brand-400 bg-brand-50" : "border-slate-200 hover:bg-slate-50"
                }`}
              >
                <button className="flex-1 min-w-0 text-left" onClick={() => setViewingRunId(run.id)}>
                  <div className="font-medium truncate">{run.keywords.join(", ")}</div>
                  <div className="text-slate-500 flex items-center justify-between mt-0.5">
                    <span>
                      {formatRelative(run.started_at)} · {PLATFORM_LABELS[run.platform]}
                      {platformInfo(run.platform)?.market_aware && <> · {marketLabel(run.market_gl)}</>}
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
                <button
                  className="text-slate-300 hover:text-red-600 opacity-0 group-hover:opacity-100 shrink-0"
                  title="Xoá lượt quét này"
                  disabled={run.status === "running"}
                  onClick={() => setDeleteTarget(run)}
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      <PipelineFormModal
        open={!!addTarget}
        pipeline={null}
        prefill={
          addTarget && viewingInfo?.pipeline_source_type
            ? buildPrefill(addTarget.result, addTarget.sourceTab, viewingInfo.pipeline_source_type)
            : undefined
        }
        onClose={() => setAddTarget(null)}
        onCreated={(created: Pipeline) => {
          if (addTarget) markAddedMut.mutate({ r: addTarget.result, pipelineId: created.id });
        }}
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title="Xoá lượt quét này?"
        body={<p>Xoá "{deleteTarget?.keywords.join(", ")}" khỏi lịch sử. Kênh đã thêm/bỏ qua trước đó không bị ảnh hưởng.</p>}
        confirmLabel="Xoá"
        cancelLabel="Hủy"
        onConfirm={() => deleteTarget && deleteScanRunMut.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
