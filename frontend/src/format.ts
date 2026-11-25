/** Dates in the business time zone the agent plans in (from the board), e.g. "20 Nov", "3 pm". */

function parts(iso: string, tz: string | undefined, opts: Intl.DateTimeFormatOptions): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  try {
    return new Intl.DateTimeFormat("en-GB", { ...opts, timeZone: tz }).format(d);
  } catch {
    return new Intl.DateTimeFormat("en-GB", opts).format(d);
  }
}

export function shortDate(iso: string | null | undefined, tz?: string): string {
  return iso ? parts(iso, tz, { day: "numeric", month: "short" }) : "";
}

export function weekdayDate(iso: string | null | undefined, tz?: string): string {
  return iso ? parts(iso, tz, { weekday: "short", day: "numeric", month: "short" }) : "";
}

export function timeOfDay(iso: string | null | undefined, tz?: string): string {
  if (!iso) return "";
  const t = parts(iso, tz, { hour: "numeric", minute: "2-digit", hour12: true });
  return t.replace(":00", "").replace(/\s?([ap])\.?m\.?/i, " $1m").toLowerCase();
}

export function dateTime(iso: string | null | undefined, tz?: string): string {
  if (!iso) return "";
  return `${weekdayDate(iso, tz)}, ${timeOfDay(iso, tz)}`;
}

/** "2026-11-24" (a calendar day from the API) -> "Mon 24 Nov". */
export function calendarDay(day: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return "";
  return weekdayDate(`${day}T12:00:00Z`, "UTC");
}
