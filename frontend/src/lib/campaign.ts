import type { Board, BoardLead, CellStatus, ScheduleRow } from "../types";

/** Pure helpers behind the Campaign page (labels, counts, looks). No React, no I/O. */

const HAS_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i;

/** "2026-11-24T04:30:00Z" (an instant) or "2026-11-24T10:00" (already business time) as a Date + zone. */
function instant(at: string, tz: string): { date: Date; zone: string } {
  if (HAS_ZONE.test(at)) return { date: new Date(at), zone: tz };
  return { date: new Date(`${at}Z`), zone: "UTC" };
}

function formatAt(at: string, tz: string): string {
  const { date, zone } = instant(at, tz);
  if (Number.isNaN(date.getTime())) return "";
  let fmt: Intl.DateTimeFormat;
  try {
    fmt = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit",
      minute: "2-digit", hourCycle: "h23", timeZone: zone });
  } catch {
    return "";
  }
  const p = Object.fromEntries(fmt.formatToParts(date).map((x) => [x.type, x.value]));
  return `${p.weekday} ${p.day} ${p.month}, ${p.hour}:${p.minute}`;
}

/** When an email goes, in a few words: "Right away", "Day 9", "Tue 24 Nov, 10:00". */
export function timingLabel(row: ScheduleRow, tz: string): string {
  if (row.mode === "now") return "Right away";
  if (row.mode === "at") return row.at ? formatAt(row.at, tz) : "";
  return `Day ${row.day}`;
}

export interface StripItem {
  emailNo: number;
  name: string;
  row: ScheduleRow;
}

export function stripItems(rows: ScheduleRow[]): StripItem[] {
  return [...rows]
    .sort((a, b) => a.email_no - b.email_no)
    .map((row) => ({ emailNo: row.email_no, name: row.template_name, row }));
}

export interface HeaderState {
  label: "Sending" | "Paused" | "Not started";
  action: "pause" | "start";
}

export function campaignHeaderState(c: Board["campaign"]): HeaderState {
  if (c.running) return { label: "Sending", action: "pause" };
  if (c.started_at) return { label: "Paused", action: "start" };
  return { label: "Not started", action: "start" };
}

export interface CellLook {
  icon: "sent" | "today" | "planned" | "cancelled";
  tone: "success" | "warning" | "muted";
}

const CELL_LOOK: Record<CellStatus, CellLook> = {
  sent: { icon: "sent", tone: "success" },
  today: { icon: "today", tone: "warning" },
  planned: { icon: "planned", tone: "muted" },
  handed: { icon: "planned", tone: "muted" },
  cancelled: { icon: "cancelled", tone: "muted" },
  skipped: { icon: "cancelled", tone: "muted" },
};

export function cellLook(status: CellStatus): CellLook {
  return CELL_LOOK[status];
}

export type TabKey = "all" | "today" | "replied" | "stopped";
export type TabCounts = Record<TabKey, number>;

const sendsToday = (l: BoardLead) => l.emails.some((e) => e.status === "today");

/** Whether a lead belongs on a tab. */
export function inTab(lead: BoardLead, tab: TabKey): boolean {
  switch (tab) {
    case "today": return sendsToday(lead);
    case "replied": return lead.stop_reason === "replied";
    case "stopped": return lead.stop_reason !== null;
    default: return true;
  }
}

export function tabCounts(leads: BoardLead[]): TabCounts {
  return {
    all: leads.length,
    today: leads.filter((l) => inTab(l, "today")).length,
    replied: leads.filter((l) => inTab(l, "replied")).length,
    stopped: leads.filter((l) => inTab(l, "stopped")).length,
  };
}

/** Case-insensitive match on name and company; an empty query matches everyone. */
export function matchesQuery(lead: BoardLead, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return lead.name.toLowerCase().includes(q) || lead.company.toLowerCase().includes(q);
}

export const CLIP_AT = 28;

/** Text cut to `max` characters with an ellipsis; `clipped` says whether a tooltip should show the rest. */
export function clip(text: string, max: number = CLIP_AT): { text: string; clipped: boolean } {
  if (text.length <= max) return { text, clipped: false };
  return { text: `${text.slice(0, max - 1).trimEnd()}…`, clipped: true };
}

/** "Ritika Sharma" -> "RS"; one word -> its first two letters. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}
