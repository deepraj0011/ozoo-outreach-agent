import type { ExampleDate, ScheduleRow } from "../types";
import { calendarDay } from "../format";
import { timingLabel } from "./campaign";

/** Pure helpers behind the schedule ruler and the list editor. No React, no I/O. */

/** The most emails a schedule can have (the API refuses more: p0/schedule.py MAX_EMAILS). */
export const MAX_EMAILS = 15;

const DAY_MS = 86_400_000;
const WEEK = 7;
const MIN_GAP_PCT = 2.5; // default: emails on the same or close days stay this far apart on the ruler
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const ISO_DAY = /^\d{4}-\d{2}-\d{2}/;

/** The calendar day ("YYYY-MM-DD") an instant falls on in the business time zone. */
export function businessDate(iso: string, tz: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" })
      .format(d);
  } catch {
    return d.toISOString().slice(0, 10);
  }
}

const dayNumber = (day: string): number => Date.parse(`${day.slice(0, 10)}T00:00:00Z`) / DAY_MS;

/** Whole calendar days from `from` to `to` (both "YYYY-MM-DD"). */
export function daysBetween(from: string, to: string): number {
  return Math.round(dayNumber(to) - dayNumber(from));
}

function addDays(day: string, n: number): string {
  return new Date((dayNumber(day) + n) * DAY_MS).toISOString().slice(0, 10);
}

const weekday = (day: string): number => new Date(`${day.slice(0, 10)}T12:00:00Z`).getUTCDay();

/** Anything with a schedule row's timing: a saved row or an unsaved one from the editor. */
export interface Timing {
  mode: ScheduleRow["mode"];
  day: number | string;
  at: string | null;
}

/** The day (0 = the day a lead starts) an email goes on, for a lead added `today`: Now = 0, a Day email its day,
 * a fixed date its offset from today (a past one counts as 0). null when the row cannot be read yet. */
export function rowOffset(row: Timing, today: string): number | null {
  if (row.mode === "now") return 0;
  if (row.mode === "at") return row.at && ISO_DAY.test(row.at) ? Math.max(0, daysBetween(today, row.at)) : null;
  const raw = String(row.day).trim();
  return /^\d+$/.test(raw) ? Number(raw) : null;
}

/** "Now", "Day 4", "Fri 4 Dec, 10:00". */
export function whenLabel(row: ScheduleRow, tz: string): string {
  return row.mode === "now" ? "Now" : timingLabel(row, tz);
}

export interface RulerMark {
  emailNo: number;
  kind: ScheduleRow["mode"];
  offset: number;
  pct: number;
  above: boolean;
  when: string;
  name: string;
  date: string;
}

export interface RulerLayout {
  marks: RulerMark[];
  span: number;
  lastDay: number;
  endDate: string;
}

/** Spread marks so none sits closer than MIN_GAP_PCT to the one before, then fit them back inside 0..100. */
function spread(pcts: number[], minGap: number): number[] {
  const out = pcts.reduce<number[]>((acc, p) => [...acc, acc.length ? Math.max(p, acc[acc.length - 1] + minGap) : p],
    []);
  const last = out.length ? out[out.length - 1] : 0;
  return last > 100 ? out.map((p) => (p * 100) / last) : out;
}

/** Where each email sits on the day ruler, for a lead added `today`. The axis spans whole weeks. `minGapPct`: the
 * closest two neighbours may sit (labels alternate above / below, so this leaves room for half a label). */
export function rulerLayout(rows: ScheduleRow[], example: ExampleDate[], today: string, tz: string,
  minGapPct: number = MIN_GAP_PCT): RulerLayout {
  const sorted = [...rows].sort((a, b) => a.email_no - b.email_no);
  const offsets = sorted.map((r) => rowOffset(r, today) ?? 0);
  const lastDay = offsets.length ? Math.max(...offsets) : 0;
  const span = Math.max(WEEK, Math.ceil((lastDay + 1) / WEEK) * WEEK);
  const pcts = spread(offsets.map((o) => (o / span) * 100), minGapPct);
  const dateOf = (n: number) => calendarDay(example.find((e) => e.email_no === n)?.date ?? "");
  const marks = sorted.map((r, i) => ({
    emailNo: r.email_no, kind: r.mode, offset: offsets[i], pct: pcts[i], above: i % 2 === 0,
    when: whenLabel(r, tz), name: r.template_name, date: dateOf(r.email_no),
  }));
  const last = marks.reduce<RulerMark | null>((a, m) => (!a || m.offset >= a.offset ? m : a), null);
  return { marks, span, lastDay, endDate: last?.date ?? "" };
}

export interface WeekBand {
  week: number;
  from: number;
  to: number;
  shaded: boolean;
}

/** Alternating week bands across a ruler of `span` days (a multiple of 7), as percentages. */
export function weekBands(span: number): WeekBand[] {
  const weeks = Math.max(1, Math.ceil(span / WEEK));
  return Array.from({ length: weeks }, (_, i) => ({
    week: i + 1, from: ((i * WEEK) / span) * 100, to: Math.min(100, (((i + 1) * WEEK) / span) * 100),
    shaded: i % 2 === 1,
  }));
}

/** "+4 days" since the previous email, "same day", or "" when either is unknown (or for email 1). */
export function gapLabel(prev: number | null, cur: number | null): string {
  if (prev === null || cur === null) return "";
  const gap = cur - prev;
  if (gap === 0) return "same day";
  if (gap < 0) return `${gap} days`;
  return `+${gap} ${gap === 1 ? "day" : "days"}`;
}

/** A short note under the real date: a weekend Day email moved to the next working day, or a fixed date that is
 * the same for every lead. "" when there is nothing to say. */
export function weekendNote(row: Timing, ex: ExampleDate | undefined, today: string): string {
  if (row.mode === "at") return row.at ? "Same moment for every lead" : "";
  if (row.mode !== "day" || !ex?.date) return "";
  const day = rowOffset(row, today);
  if (day === null) return "";
  const plain = addDays(today, day);
  const wd = weekday(plain);
  if ((wd !== 0 && wd !== 6) || ex.date === plain) return "";
  return `Day ${day} is a ${WEEKDAYS[wd]}: goes ${WEEKDAYS[weekday(ex.date)]}`;
}
