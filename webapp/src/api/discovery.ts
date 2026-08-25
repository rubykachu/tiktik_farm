import { api } from "./client";

export interface DiscoveryKeyword {
  id: number;
  keyword: string;
  created_at: string;
  last_scanned_at: string | null;
}

export interface DiscoveryCandidate {
  id: number;
  channel_id: string;
  channel_url: string;
  channel_title: string;
  found_via_keyword: string;
  subscriber_count: number;
  median_view_count: number;
  days_since_last_video: number;
  cadence_days_per_video: number | null;
  cadence_tab: "videos" | "shorts" | null;
  has_shorts_tab: boolean;
  recent_shorts_count: number;
  score: number;
  status: "new" | "added" | "skipped";
  added_pipeline_id: number | null;
  created_at: string;
  last_scanned_at: string;
}

export interface DiscoveryScanRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  keywords: string[];
  channels_total: number;
  channels_done: number;
  candidates_found: number;
  status: "running" | "done" | "error";
  error_text: string | null;
}

export interface DiscoverySettings {
  min_subscribers: number;
  silent_months: number;
  target_days_per_video: number;
  scans_per_day: number;
  keywords_per_scan: number;
  market_gl: string;
  market_hl: string;
  view_score_reference: number;
}

export type DiscoverySettingsUpdate = Partial<DiscoverySettings>;

export const Discovery = {
  getSettings: () => api.get<DiscoverySettings>("/discovery/settings").then((r) => r.data),
  patchSettings: (payload: DiscoverySettingsUpdate) =>
    api.patch<DiscoverySettings>("/discovery/settings", payload).then((r) => r.data),

  listKeywords: () => api.get<DiscoveryKeyword[]>("/discovery/keywords").then((r) => r.data),
  addKeyword: (keyword: string) =>
    api.post<DiscoveryKeyword>("/discovery/keywords", { keyword }).then((r) => r.data),
  removeKeyword: (id: number) => api.delete(`/discovery/keywords/${id}`),
  suggestKeywords: (seed: string) =>
    api
      .get<{ suggestions: string[] }>("/discovery/keyword-suggestions", { params: { seed } })
      .then((r) => r.data.suggestions),

  listCandidates: () => api.get<DiscoveryCandidate[]>("/discovery/candidates").then((r) => r.data),
  skipCandidate: (id: number) =>
    api.post<DiscoveryCandidate>(`/discovery/candidates/${id}/skip`).then((r) => r.data),
  markCandidateAdded: (id: number, pipelineId: number) =>
    api
      .post<DiscoveryCandidate>(`/discovery/candidates/${id}/mark-added`, { pipeline_id: pipelineId })
      .then((r) => r.data),

  scanStatus: () => api.get<DiscoveryScanRun | null>("/discovery/scan-status").then((r) => r.data),
  scanNow: () => api.post<{ queued: boolean }>("/discovery/scan-now").then((r) => r.data),
};
