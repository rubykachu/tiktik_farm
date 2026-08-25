import { api } from "./client";

export type ManualDownloadStatus = "queued" | "downloading" | "ready" | "failed";

export interface ManualDownload {
  id: number;
  url: string;
  source_type: string;
  origin: "manual" | "pipeline";
  status: ManualDownloadStatus;
  title: string | null;
  file_size: number | null;
  duration_seconds: number | null;
  ai_caption: string | null;
  error_text: string | null;
  created_at: string;
  expires_at: string;
}

export const Downloads = {
  list: () => api.get<ManualDownload[]>("/downloads").then((r) => r.data),

  create: (urls: string[]) =>
    api.post<ManualDownload[]>("/downloads", { urls }).then((r) => r.data),

  caption: (id: number, prompt?: string) =>
    api
      .post<ManualDownload>(`/downloads/${id}/caption`, { prompt })
      .then((r) => r.data),

  remove: (id: number) => api.delete(`/downloads/${id}`),

  // Served as an attachment, so a plain navigation is enough to save it.
  fileUrl: (id: number) => `/api/downloads/${id}/file`,
};
