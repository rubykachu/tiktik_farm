import { api } from "./client";

export type DiscoveryPlatform = "youtube" | "tiktok" | "douyin";

/** What one platform can do. Served by the API so the rules live in one
 *  place (api/services/discovery_sources) instead of being restated here. */
export interface DiscoveryPlatformInfo {
  platform: DiscoveryPlatform;
  /** One posting cadence instead of YouTube's Shorts / video-dài pair. */
  single_feed: boolean;
  /** Whether the market picker changes anything for this platform. */
  market_aware: boolean;
  supports_suggestions: boolean;
  /** null when a candidate cannot be turned into a pipeline at all. */
  pipeline_source_type: "youtube" | "tiktok" | "douyin" | null;
}

export interface DiscoveryScanRun {
  id: number;
  platform: DiscoveryPlatform;
  keywords: string[];
  market_gl: string;
  market_hl: string;
  started_at: string;
  finished_at: string | null;
  channels_total: number;
  channels_done: number;
  candidates_found: number;
  status: "running" | "done" | "error";
  error_text: string | null;
}

export interface DiscoveryScanResult {
  id: number;
  channel_id: string;
  channel_url: string;
  channel_title: string;
  found_via_keyword: string;
  subscriber_count: number;
  median_view_count: number;
  days_since_last_video: number;
  latest_video_posted_at: string | null;
  cadence_videos_days_per_video: number | null;
  cadence_shorts_days_per_video: number | null;
  score: number;
  channel_description: string | null;
  median_duration_seconds: number | null;
  status: "new" | "added" | "skipped";
  added_pipeline_id: number | null;
}

export interface DiscoverySettings {
  min_subscribers: number;
  silent_months: number;
  target_days_per_video: number;
  view_score_reference: number;
}

export type DiscoverySettingsUpdate = Partial<DiscoverySettings>;

export const DISCOVERY_MAX_KEYWORDS_PER_SCAN = 5;

export const Discovery = {
  listPlatforms: () => api.get<DiscoveryPlatformInfo[]>("/discovery/platforms").then((r) => r.data),

  getSettings: () => api.get<DiscoverySettings>("/discovery/settings").then((r) => r.data),
  patchSettings: (payload: DiscoverySettingsUpdate) =>
    api.patch<DiscoverySettings>("/discovery/settings", payload).then((r) => r.data),

  suggestKeywords: (platform: DiscoveryPlatform, seed: string, gl: string, hl: string) =>
    api
      .get<{ suggestions: string[] }>("/discovery/keyword-suggestions", {
        params: { platform, seed, gl, hl },
      })
      .then((r) => r.data.suggestions),

  scanNow: (platform: DiscoveryPlatform, keywords: string[], marketGl: string, marketHl: string) =>
    api
      .post<{ scan_run_id: number; status: string }>("/discovery/scan-now", {
        platform,
        keywords,
        market_gl: marketGl,
        market_hl: marketHl,
      })
      .then((r) => r.data),

  listScanRuns: () => api.get<DiscoveryScanRun[]>("/discovery/scan-runs").then((r) => r.data),
  getScanRun: (id: number) => api.get<DiscoveryScanRun>(`/discovery/scan-runs/${id}`).then((r) => r.data),
  deleteScanRun: (id: number) => api.delete(`/discovery/scan-runs/${id}`),
  listScanResults: (scanRunId: number) =>
    api.get<DiscoveryScanResult[]>(`/discovery/scan-runs/${scanRunId}/results`).then((r) => r.data),

  skipChannel: (platform: DiscoveryPlatform, channelId: string, channelUrl: string, channelTitle: string) =>
    api
      .post(`/discovery/channels/${channelId}/skip`, {
        platform,
        channel_url: channelUrl,
        channel_title: channelTitle,
      })
      .then((r) => r.data),
  markChannelAdded: (
    platform: DiscoveryPlatform,
    channelId: string,
    channelUrl: string,
    channelTitle: string,
    pipelineId: number,
  ) =>
    api
      .post(`/discovery/channels/${channelId}/mark-added`, {
        platform,
        channel_url: channelUrl,
        channel_title: channelTitle,
        pipeline_id: pipelineId,
      })
      .then((r) => r.data),
};
