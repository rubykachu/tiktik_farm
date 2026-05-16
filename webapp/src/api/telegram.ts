import { api } from "./client";

export interface TelegramChatItem {
  id: number;
  title: string;
  type: string;
}

export const Telegram = {
  detectChats: (bot_token: string) =>
    api
      .post<{ chats: TelegramChatItem[] }>("/telegram/detect-chats", { bot_token })
      .then((r) => r.data),

  sendTest: (bot_token: string, chat_id: string) =>
    api
      .post<{ ok: boolean; message: string }>("/telegram/test", { bot_token, chat_id })
      .then((r) => r.data),
};
