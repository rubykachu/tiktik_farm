import { api } from "./client";

export interface Pipeline {
  id: number;
  name: string;
  channel_url: string;
  source_type: "youtube" | "douyin";
  fetch_count: number;
  interval_value: number;
  interval_unit: "minutes" | "hours" | "days";
  first_fire_at: string;
  next_fire_at: string;
  state: "active" | "paused";
  caption_template: string;
  upload_options_json: string;
  account_ids_json: string;
  max_duration_seconds: number | null;
  upload_delay_minutes: number;
  caption_ai_prompt: string | null;
  window_end_time: string | null;
  window_delay_minutes: number | null;
  douyin_cookie_id: number | null;
  telegram_bot_token_set: boolean;
  telegram_chat_id: string | null;
  run_now_requested_at: string | null;
  lock_until: string | null;
  created_at: string;
  updated_at: string;
}

export interface PipelineCreate {
  name: string;
  channel_url: string;
  source_type?: "youtube" | "douyin";
  douyin_cookie_id?: number | null;
  fetch_count: number;
  interval_value: number;
  interval_unit: "minutes" | "hours" | "days";
  first_fire_at: string;
  caption_template: string;
  upload_options: {
    allow_comment: 0 | 1;
    allow_duet: 0 | 1;
    allow_stitch: 0 | 1;
    visibility_type: 0 | 1;
  };
  account_ids: number[];
  max_duration_seconds?: number | null;
  upload_delay_minutes?: number;
  caption_ai_prompt?: string | null;
  window_end_time?: string | null;
  window_delay_minutes?: number | null;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
}

export interface PipelineUpdate {
  name?: string;
  state?: "active" | "paused";
  channel_url?: string;
  douyin_cookie_id?: number | null;
  fetch_count?: number;
  interval_value?: number;
  interval_unit?: "minutes" | "hours" | "days";
  first_fire_at?: string;
  caption_template?: string;
  upload_options?: PipelineCreate["upload_options"];
  account_ids?: number[];
  max_duration_seconds?: number | null;
  upload_delay_minutes?: number;
  caption_ai_prompt?: string | null;
  window_end_time?: string | null;
  window_delay_minutes?: number | null;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
  clear_bot_token?: boolean;
}

export interface PipelineRunItem {
  id: number;
  pipeline_id: number;
  started_at: string | null;
  finished_at: string | null;
  trigger_reason: string;
  pulled_count: number;
  uploaded_count: number;
  skipped_dup_count: number;
  skipped_exhausted_count: number;
  failed_count: number;
  summary_text: string;
  error_text: string | null;
  status_text: string | null;
}

export interface VideoUploadItem {
  id: number;
  account_id: number;
  youtube_video_id: string;
  youtube_url: string;
  youtube_title: string | null;
  douyin_aweme_id: string | null;
  tiktok_video_id: string | null;
  tiktok_url: string | null;
  rendered_caption: string | null;
  status: string;
  attempt_count: number;
  error_text: string | null;
  created_at: string | null;
}

export const Pipelines = {
  list: () => api.get<Pipeline[]>("/pipelines").then((r) => r.data),
  get: (id: number) => api.get<Pipeline>(`/pipelines/${id}`).then((r) => r.data),
  create: (p: PipelineCreate) =>
    api.post<Pipeline>("/pipelines", p).then((r) => r.data),
  update: (id: number, p: PipelineUpdate) =>
    api.patch<Pipeline>(`/pipelines/${id}`, p).then((r) => r.data),
  remove: (id: number) => api.delete(`/pipelines/${id}`),
  runNow: (id: number) =>
    api.post<{ ok: boolean; message: string }>(`/pipelines/${id}/run-now`).then((r) => r.data),
  listRuns: (pipelineId: number, page = 1) =>
    api.get<PipelineRunItem[]>(`/pipelines/${pipelineId}/runs`, { params: { page } }).then((r) => r.data),
  listRunVideos: (pipelineId: number, runId: number) =>
    api.get<VideoUploadItem[]>(`/pipelines/${pipelineId}/runs/${runId}/videos`).then((r) => r.data),
};
