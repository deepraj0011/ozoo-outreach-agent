import { Chip } from "@mui/material";
import { OUTCOME_LABEL } from "../lib/leads";
import { TONE_SX } from "./status";

export default function OutcomeChip({ outcome }: { outcome: string }) {
  const o = OUTCOME_LABEL[outcome] ?? { label: outcome, tone: "muted" as const };
  return <Chip size="small" label={o.label} sx={{ ...TONE_SX[o.tone], fontWeight: 600, fontSize: 12 }} />;
}
