import { api } from "./client";

export interface TelegramChatItem {
  id: number;
  title: string;
  type: string;
}

export interface TelegramAttachField {
  key: string;
  label: string;
}

export const Telegram = {
  attachFields: () =>
    api
      .get<{ fields: TelegramAttachField[] }>("/telegram/attach-fields")
      .then((r) => r.data.fields),

  detectChats: (bot_token: string) =>
    api
      .post<{ chats: TelegramChatItem[] }>("/telegram/detect-chats", { bot_token })
      .then((r) => r.data),

  sendTest: (bot_token: string, chat_id: string) =>
    api
      .post<{ ok: boolean; message: string }>("/telegram/test", { bot_token, chat_id })
      .then((r) => r.data),

  resolveChat: (bot_token: string, chat_id: string) =>
    api
      .post<{ name: string | null; type: string | null }>("/telegram/resolve-chat", { bot_token, chat_id })
      .then((r) => r.data),
};
