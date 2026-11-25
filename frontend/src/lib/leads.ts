import type { NewLead } from "../types";

const EMAIL_SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Field name -> message. An empty object means the lead can be sent. */
export function validateNewLead(input: NewLead): Record<string, string> {
  const errors: Record<string, string> = {};
  const email = (input.email ?? "").trim();
  const phone = (input.phone ?? "").trim();
  if (!input.name.trim()) errors.name = "Add a name.";
  if (!input.company.trim()) errors.company = "Add a company.";
  if (email && !EMAIL_SHAPE.test(email)) errors.email = "This email does not look right.";
  else if (!email && !phone) errors.email = "Add an email or a phone number.";
  return errors;
}

export type OutcomeTone = "success" | "warning" | "error" | "accent" | "muted";

/** How an intake or import outcome reads in a chip. */
export const OUTCOME_LABEL: Record<string, { label: string; tone: OutcomeTone }> = {
  accepted: { label: "Added", tone: "success" },
  skipped_duplicate: { label: "Duplicate", tone: "warning" },
  skipped_suppressed: { label: "Opted out", tone: "warning" },
  rejected: { label: "Rejected", tone: "error" },
  skipped: { label: "Rejected", tone: "error" },
  error: { label: "Error", tone: "error" },
  held_eu: { label: "Added, on hold", tone: "accent" },
  held_verify: { label: "Added, on hold", tone: "accent" },
  held_account: { label: "Added, on hold", tone: "accent" },
};

export const isAdded = (outcome: string) => outcome === "accepted" || outcome.startsWith("held");

export function importTotals(summary: Record<string, number>) {
  const n = (k: string) => summary[k] ?? 0;
  return {
    added: n("accepted") + n("held_eu") + n("held_verify") + n("held_account"),
    duplicates: n("skipped_duplicate"),
    rejected: n("rejected") + n("skipped") + n("error") + n("skipped_suppressed"),
  };
}
