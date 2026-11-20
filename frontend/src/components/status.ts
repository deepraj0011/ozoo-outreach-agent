import type { BoardLead, StopReason } from "../types";

export type ChipColor = "default" | "primary" | "success" | "warning" | "error" | "info" | "secondary";

export const STOP_LABEL: Record<StopReason, string> = {
  replied: "Replied",
  unsubscribed: "Unsubscribed",
  bounced: "Bounced",
  booked: "Demo booked",
  stopped: "Stopped",
};

const STOP_COLOR: Record<StopReason, ChipColor> = {
  replied: "secondary",
  unsubscribed: "error",
  bounced: "error",
  booked: "success",
  stopped: "default",
};

const STATE_LABEL: Record<string, string> = {
  intake: "New",
  in_sequence: "Receiving emails",
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

export function leadStatus(lead: BoardLead): { label: string; color: ChipColor } {
  if (lead.stop_reason) return { label: STOP_LABEL[lead.stop_reason], color: STOP_COLOR[lead.stop_reason] };
  if (lead.paused) return { label: "Paused", color: "warning" };
  if (lead.state === "in_sequence") {
    const left = lead.emails.some((e) => ["planned", "today", "handed"].includes(e.status));
    return left ? { label: "Receiving emails", color: "primary" } : { label: "All sent", color: "success" };
  }
  if (lead.state === "paused") return { label: "On hold", color: "warning" };
  return { label: STATE_LABEL[lead.state] ?? lead.state, color: "default" };
}

/** Pause / resume / send now / stop only work while the lead is still on the schedule. */
export function canControl(lead: BoardLead): boolean {
  return !lead.stop_reason && (lead.state === "in_sequence" || lead.state === "paused");
}
