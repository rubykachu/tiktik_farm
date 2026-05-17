import { api } from "./client";

export interface AppSettings {
  llm_enabled: boolean;
}

export interface LLMTestResult {
  ok: boolean;
  message: string;
}

export const Settings = {
  get: () => api.get<AppSettings>("/settings").then((r) => r.data),
  patch: (payload: Partial<AppSettings>) =>
    api.patch<AppSettings>("/settings", payload).then((r) => r.data),
  testLLM: (model_id: string, api_key: string) =>
    api
      .post<LLMTestResult>("/settings/llm/test", { model_id, api_key })
      .then((r) => r.data),
};
