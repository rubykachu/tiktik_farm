import { api } from "./client";

export type TelegramNotifyMode = "changes_only" | "cycle" | "every_tick";

export type LastRunStatus = "ok" | "partial" | "failed" | "error" | "running";

export interface PipelineLastRun {
  at: string | null;
  status: LastRunStatus;
  uploaded_count: number;
  failed_count: number;
  error_text: string | null;
}

export interface Pipeline {
  id: number;
  last_run: PipelineLastRun | null;
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
  channel_description: string | null;
  window_end_time: string | null;
  window_delay_minutes: number | null;
  douyin_cookie_id: number | null;
  telegram_bot_token_set: boolean;
  telegram_chat_id: string | null;
  telegram_notify_mode: TelegramNotifyMode;
  telegram_attach_video: boolean;
  telegram_attach_fields: string[];
  run_now_requested_at: string | null;
  lock_until: string | null;
  created_at: string;
  updated_at: string;
  sync_from_date: string | null;
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
  // Comment / duet / stitch / visibility are not exposed in the UI; omit the
  // field and the server applies its PipelineUploadOptions defaults.
  upload_options?: {
    allow_comment: 0 | 1;
    allow_duet: 0 | 1;
    allow_stitch: 0 | 1;
    visibility_type: 0 | 1;
  };
  account_ids: number[];
  max_duration_seconds?: number | null;
  upload_delay_minutes?: number;
  caption_ai_prompt?: string | null;
  channel_description?: string | null;
  window_end_time?: string | null;
  window_delay_minutes?: number | null;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
  telegram_notify_mode?: TelegramNotifyMode;
  telegram_attach_video?: boolean;
  telegram_attach_fields?: string[];
  sync_from_date?: string | null;
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
  channel_description?: string | null;
  window_end_time?: string | null;
  window_delay_minutes?: number | null;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
  clear_bot_token?: boolean;
  telegram_notify_mode?: TelegramNotifyMode;
  telegram_attach_video?: boolean;
  telegram_attach_fields?: string[];
  sync_from_date?: string | null;
}

/** One calendar day of runs, as the operator's timezone sees it. */
export interface RunDay {
  date: string;
  items: PipelineRunItem[];
  /** Rows this day actually has — `items` is capped at `per_day_limit`. */
  total: number;
  /** Videos uploaded across the whole day, not just the loaded rows. */
  uploaded_total: number;
}

export interface RunDays {
  days: RunDay[];
  day_limit: number;
  per_day_limit: number;
}

/** Response to a `date=` request: more rows inside one already-shown day. */
export interface RunDayPage {
  date: string;
  items: PipelineRunItem[];
  total: number;
  offset: number;
}

export interface PipelinePage {
  items: Pipeline[];
  total: number;
  page: number;
  size: number;
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
  pushed_notify_at: string | null;
}

export interface VideoUploadItem {
  id: number;
  account_id: number;
  youtube_video_id: string;
  youtube_url: string;
  source_title: string | null;
  douyin_aweme_id: string | null;
  tiktok_video_id: string | null;
  tiktok_url: string | null;
  rendered_caption: string | null;
  ai_title: string | null;
  status: string;
  attempt_count: number;
  error_text: string | null;
  created_at: string | null;
}

export const Pipelines = {
  list: (page = 1) =>
    api.get<PipelinePage>("/pipelines", { params: { page } }).then((r) => r.data),

  /** Every pipeline, for pickers that need the whole set rather than a page. */
  listAll: async () => {
    const first = await api
      .get<PipelinePage>("/pipelines")
      .then((r) => r.data);
    const items = [...first.items];
    const pages = Math.ceil(first.total / first.size);
    for (let page = 2; page <= pages; page++) {
      const next = await api
        .get<PipelinePage>("/pipelines", { params: { page } })
        .then((r) => r.data);
      items.push(...next.items);
    }
    return items;
  },
  get: (id: number) => api.get<Pipeline>(`/pipelines/${id}`).then((r) => r.data),
  create: (p: PipelineCreate) =>
    api.post<Pipeline>("/pipelines", p).then((r) => r.data),
  update: (id: number, p: PipelineUpdate) =>
    api.patch<Pipeline>(`/pipelines/${id}`, p).then((r) => r.data),
  remove: (id: number) => api.delete(`/pipelines/${id}`),
  runNow: (id: number) =>
    api.post<{ ok: boolean; message: string }>(`/pipelines/${id}/run-now`).then((r) => r.data),
  listRuns: (pipelineId: number) =>
    api.get<RunDays>(`/pipelines/${pipelineId}/runs`).then((r) => r.data),

  listRunsForDay: (pipelineId: number, date: string, offset: number) =>
    api
      .get<RunDayPage>(`/pipelines/${pipelineId}/runs`, { params: { date, offset } })
      .then((r) => r.data),
  listRunVideos: (pipelineId: number, runId: number) =>
    api.get<VideoUploadItem[]>(`/pipelines/${pipelineId}/runs/${runId}/videos`).then((r) => r.data),
  clearRuns: (pipelineId: number, runIds: number[]) => {
    const params = new URLSearchParams();
    runIds.forEach((id) => params.append("run_ids", String(id)));
    return api.delete(`/pipelines/${pipelineId}/runs?${params}`);
  },
};
