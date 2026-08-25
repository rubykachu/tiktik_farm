import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Telegram, type TelegramChatItem } from "../api/telegram";

const CHAT_ID_RE = /^-?\d+$/;

/**
 * Resolves a friendly display name for a Chat ID field. Checked in order:
 * 1. `knownChats` (the current "Dò chat" result) — free, no request.
 * 2. Telegram's getChat, for a chat_id the bot has any history with even if
 *    it wasn't in the recent-activity detect list (e.g. pasted in by hand,
 *    or from a previous session).
 * Returns null while unresolved (including: no bot token yet, chat_id not
 * numeric, or the bot has no history with that chat at all) — the caller
 * just shows nothing extra in that case, never an error.
 */
export function useTelegramChatName(
  botToken: string,
  chatId: string,
  knownChats?: TelegramChatItem[] | null,
): string | null {
  const trimmedToken = botToken.trim();
  const trimmedChat = chatId.trim();

  const known = useMemo(
    () => knownChats?.find((c) => String(c.id) === trimmedChat) ?? null,
    [knownChats, trimmedChat],
  );

  const { data } = useQuery({
    queryKey: ["telegram-resolve-chat", trimmedToken, trimmedChat],
    queryFn: () => Telegram.resolveChat(trimmedToken, trimmedChat),
    enabled: !known && !!trimmedToken && CHAT_ID_RE.test(trimmedChat),
    staleTime: 5 * 60_000,
    retry: false,
  });

  if (known) return `${known.title} (${known.type})`;
  if (data?.name) return data.type ? `${data.name} (${data.type})` : data.name;
  return null;
}
