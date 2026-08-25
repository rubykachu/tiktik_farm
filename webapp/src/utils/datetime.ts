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
