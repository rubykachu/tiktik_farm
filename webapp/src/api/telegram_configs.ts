import { api } from "./client";

export interface TelegramConfig {
  id: number;
  name: string;
  bot_token: string;
  chat_id: string;
  created_at: string;
}

export const TelegramConfigs = {
  list: () => api.get<TelegramConfig[]>("/telegram-configs").then((r) => r.data),
  create: (name: string, bot_token: string, chat_id: string) =>
    api
      .post<TelegramConfig>("/telegram-configs", { name, bot_token, chat_id })
      .then((r) => r.data),
  remove: (id: number) => api.delete(`/telegram-configs/${id}`),
};
