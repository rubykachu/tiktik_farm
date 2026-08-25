/**
 * Parse a timestamp returned by the API.
 *
 * SQLite drops tzinfo, so the API can emit a naive ISO string. Without an
 * explicit zone the browser reads it as local time and every duration is off
 * by the UTC offset — append "Z" when the string carries no zone.
 */
export function asUtcDate(s: string): Date {
  return new Date(/Z|[+-]\d{2}:\d{2}$/.test(s) ? s : s + "Z");
}

/** "25/08/2026 14:32" — day-first, no seconds, matching the Vietnamese UI. */
export function formatDateTime(s: string | null | undefined): string {
  if (!s) return "—";
  return asUtcDate(s).toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "14:32" — for timestamps whose date is already obvious from context. */
export function formatTime(s: string | null | undefined): string {
  if (!s) return "—";
  return asUtcDate(s).toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * "3 phút trước", "sau 2 giờ nữa" — a duration is easier to judge than a
 * timestamp when the question is "did this just happen / is it soon?".
 */
export function formatRelative(s: string | null | undefined): string {
  if (!s) return "—";
  const diffMs = asUtcDate(s).getTime() - Date.now();
  const future = diffMs > 0;
  const mins = Math.round(Math.abs(diffMs) / 60_000);
  if (mins < 1) return future ? "sắp tới" : "vừa xong";

  let value: string;
  if (mins < 60) value = `${mins} phút`;
  else if (mins < 60 * 24) value = `${Math.round(mins / 60)} giờ`;
  else value = `${Math.round(mins / (60 * 24))} ngày`;

  return future ? `sau ${value} nữa` : `${value} trước`;
}
