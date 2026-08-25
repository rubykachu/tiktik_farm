import { api } from "./client";

export interface DiscoveryKeyword {
  id: number;
  keyword: string;
  created_at: string;
}

export interface DiscoveryScanRun {
  id: number;
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
  cadence_days_per_video: number | null;
  cadence_tab: "videos" | "shorts" | null;
  has_shorts_tab: boolean;
  recent_shorts_count: number;
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
  getSettings: () => api.get<DiscoverySettings>("/discovery/settings").then((r) => r.data),
  patchSettings: (payload: DiscoverySettingsUpdate) =>
    api.patch<DiscoverySettings>("/discovery/settings", payload).then((r) => r.data),

  listKeywords: () => api.get<DiscoveryKeyword[]>("/discovery/keywords").then((r) => r.data),
  addKeyword: (keyword: string) =>
    api.post<DiscoveryKeyword>("/discovery/keywords", { keyword }).then((r) => r.data),
  removeKeyword: (id: number) => api.delete(`/discovery/keywords/${id}`),
  suggestKeywords: (seed: string, gl: string, hl: string) =>
    api
      .get<{ suggestions: string[] }>("/discovery/keyword-suggestions", { params: { seed, gl, hl } })
      .then((r) => r.data.suggestions),

  scanNow: (keywords: string[], marketGl: string, marketHl: string) =>
    api
      .post<{ scan_run_id: number; status: string }>("/discovery/scan-now", {
        keywords,
        market_gl: marketGl,
        market_hl: marketHl,
      })
      .then((r) => r.data),

  listScanRuns: () => api.get<DiscoveryScanRun[]>("/discovery/scan-runs").then((r) => r.data),
  getScanRun: (id: number) => api.get<DiscoveryScanRun>(`/discovery/scan-runs/${id}`).then((r) => r.data),
  listScanResults: (scanRunId: number) =>
    api.get<DiscoveryScanResult[]>(`/discovery/scan-runs/${scanRunId}/results`).then((r) => r.data),

  skipChannel: (channelId: string, channelUrl: string, channelTitle: string) =>
    api
      .post(`/discovery/channels/${channelId}/skip`, { channel_url: channelUrl, channel_title: channelTitle })
      .then((r) => r.data),
  markChannelAdded: (channelId: string, channelUrl: string, channelTitle: string, pipelineId: number) =>
    api
      .post(`/discovery/channels/${channelId}/mark-added`, {
        channel_url: channelUrl,
        channel_title: channelTitle,
        pipeline_id: pipelineId,
      })
      .then((r) => r.data),
};
