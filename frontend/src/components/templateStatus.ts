import type { TemplateStatus } from "../types";
import { TONE_SX } from "./status";

/** Status chips on the Templates page: draft / live / off. */
export const TEMPLATE_STATUS: Record<TemplateStatus, { label: string; sx: { bgcolor: string; color: string } }> = {
  live: { label: "Live", sx: TONE_SX.success },
  draft: { label: "Draft", sx: TONE_SX.warning },
  off: { label: "Off", sx: TONE_SX.muted },
};

export const usedByText = (usedBy: number[]) =>
  usedBy.length ? `Used by ${usedBy.map((n) => `email ${n}`).join(", ")}` : "Not used";
