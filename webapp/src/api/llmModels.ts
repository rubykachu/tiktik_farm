import { api } from "./client";

export interface LLMModel {
  id: number;
  label: string;
  model_id: string;
  api_key_masked: string;
  api_base: string | null;
  api_version: string | null;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface LLMModelCreate {
  label: string;
  model_id: string;
  api_key: string;
  api_base?: string;
  api_version?: string;
  set_default?: boolean;
}

export interface LLMModelUpdate {
  label?: string;
  model_id?: string;
  api_key?: string;
  api_base?: string;
  api_version?: string;
  clear_api_base?: boolean;
}

export const LLMModels = {
  list: () => api.get<LLMModel[]>("/llm-models").then((r) => r.data),
  create: (payload: LLMModelCreate) =>
    api.post<LLMModel>("/llm-models", payload).then((r) => r.data),
  update: (id: number, payload: LLMModelUpdate) =>
    api.patch<LLMModel>(`/llm-models/${id}`, payload).then((r) => r.data),
  setDefault: (id: number) =>
    api.post<LLMModel>(`/llm-models/${id}/set-default`).then((r) => r.data),
  remove: (id: number) => api.delete(`/llm-models/${id}`),
};
