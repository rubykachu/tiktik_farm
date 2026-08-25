import { api } from "./client";

export interface RecentError {
  at: string | null;
  title: string | null;
  error: string | null;
  account_id: number;
}

export interface Overview {
  uploads_24h: { succeeded: number; failed: number };
  pipelines: { active: number; total: number };
  accounts: { total: number; invalid: number; invalid_usernames: string[] };
  next_fire_at: string | null;
  stuck_runs: number;
  downloads_ready: number;
  recent_errors: RecentError[];
  generated_at: string;
}

export const OverviewApi = {
  get: () => api.get<Overview>("/overview").then((r) => r.data),
};
