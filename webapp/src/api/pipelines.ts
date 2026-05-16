import { api } from "./client";

export interface Pipeline {
  id: number;
  name: string;
  channel_url: string;
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
  telegram_bot_token?: string;
  telegram_chat_id?: string;
}

export interface PipelineUpdate {
  name?: string;
  state?: "active" | "paused";
  fetch_count?: number;
  interval_value?: number;
  interval_unit?: "minutes" | "hours" | "days";
  caption_template?: string;
  upload_options?: PipelineCreate["upload_options"];
  account_ids?: number[];
  max_duration_seconds?: number | null;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
  clear_bot_token?: boolean;
}

export const Pipelines = {
  list: () => api.get<Pipeline[]>("/pipelines").then((r) => r.data),
  get: (id: number) => api.get<Pipeline>(`/pipelines/${id}`).then((r) => r.data),
  create: (p: PipelineCreate) =>
    api.post<Pipeline>("/pipelines", p).then((r) => r.data),
  update: (id: number, p: PipelineUpdate) =>
    api.patch<Pipeline>(`/pipelines/${id}`, p).then((r) => r.data),
  remove: (id: number) => api.delete(`/pipelines/${id}`),
};
