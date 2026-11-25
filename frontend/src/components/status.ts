import type { BoardLead, StopReason } from "../types";

export type ChipColor = "default" | "primary" | "success" | "warning" | "error" | "info" | "secondary";

/** Soft chip looks on the dark theme: tinted background, bright text (mockup C). */
export type ChipTone = "accent" | "success" | "warning" | "error" | "muted";

export const TONE_SX: Record<ChipTone, { bgcolor: string; color: string }> = {
  accent: { bgcolor: "#1d2e2c", color: "#2dd4bf" },
  success: { bgcolor: "#173322", color: "#4ade80" },
  warning: { bgcolor: "#3a2f12", color: "#fbbf24" },
  error: { bgcolor: "#3a1d1d", color: "#f87171" },
  muted: { bgcolor: "#202328", color: "#9aa1ab" },
};

export const STOP_LABEL: Record<StopReason, string> = {
  replied: "Replied",
  unsubscribed: "Unsubscribed",
  bounced: "Bounced",
  booked: "Demo booked",
  stopped: "Stopped",
};

const STOP_TONE: Record<StopReason, ChipTone> = {
  replied: "success",
  unsubscribed: "error",
  bounced: "error",
  booked: "success",
  stopped: "muted",
};

const TONE_COLOR: Record<ChipTone, ChipColor> = {
  accent: "primary",
  success: "success",
  warning: "warning",
  error: "error",
  muted: "default",
};

const STATE_LABEL: Record<string, string> = {
  intake: "New",
  in_sequence: "In sequence",
  paused: "On hold",
  engaged: "Talking",
  qualifying: "Qualifying",
  booked: "Demo booked",
  handed_off: "With sales",
  nurture: "Finished",
  closed: "Closed",
  suppressed: "Do not contact",
  rejected: "Rejected",
  archived: "Archived",
};

function statusTone(lead: BoardLead): { label: string; tone: ChipTone } {
  if (lead.stop_reason) return { label: STOP_LABEL[lead.stop_reason], tone: STOP_TONE[lead.stop_reason] };
  if (lead.paused) return { label: "Paused", tone: "warning" };
  if (lead.state === "in_sequence") {
    const left = lead.emails.some((e) => ["planned", "today", "handed"].includes(e.status));
    return left ? { label: "In sequence", tone: "accent" } : { label: "All sent", tone: "success" };
  }
  if (lead.state === "paused") return { label: "On hold", tone: "warning" };
  return { label: STATE_LABEL[lead.state] ?? lead.state, tone: "muted" };
}

export function leadStatus(lead: BoardLead): { label: string; color: ChipColor; tone: ChipTone } {
  const s = statusTone(lead);
  return { ...s, color: TONE_COLOR[s.tone] };
}

/** Pause / resume / send now / stop only work while the lead is still on the schedule. */
export function canControl(lead: BoardLead): boolean {
  return !lead.stop_reason && (lead.state === "in_sequence" || lead.state === "paused");
}
