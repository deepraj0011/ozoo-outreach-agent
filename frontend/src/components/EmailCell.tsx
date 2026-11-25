import { Box, ButtonBase, Stack, Tooltip, Typography } from "@mui/material";
import CheckCircleRounded from "@mui/icons-material/CheckCircleRounded";
import ScheduleSendRounded from "@mui/icons-material/ScheduleSendRounded";
import WarningAmberRounded from "@mui/icons-material/WarningAmberRounded";
import type { Cell } from "../types";
import { shortDate, timeOfDay } from "../format";
import { cellLook, type CellLook } from "../lib/campaign";

const TONE_COLOR: Record<CellLook["tone"], string> = {
  success: "success.main",
  warning: "warning.main",
  muted: "text.secondary",
};

function label(cell: Cell, tz: string): string {
  switch (cell.status) {
    case "sent": return shortDate(cell.sent_at ?? cell.date, tz);
    case "today": return `Today ${timeOfDay(cell.date, tz)}`.trim();
    case "skipped": return shortDate(cell.date, tz) || "Skipped";
    case "cancelled": return shortDate(cell.date, tz) || "Cancelled";
    default: return shortDate(cell.date, tz);
  }
}

function hint(cell: Cell): string {
  switch (cell.status) {
    case "sent": {
      const parts = [`${cell.opens} opens`, `${cell.clicks} clicks`];
      if (cell.visits) parts.push(`${cell.visits} page reads`);
      return `Sent. ${parts.join(", ")}.`;
    }
    case "today": return "Goes today";
    case "handed": return "With the sender";
    case "planned": return "Planned";
    case "skipped": return "Skipped";
    default: return cell.reason ? `Cancelled (${cell.reason.replace(/_/g, " ")})` : "Cancelled";
  }
}

export default function EmailCell({ cell, tz, onClick }: { cell: Cell | undefined; tz: string; onClick: () => void }) {
  if (!cell) return <Box component="span" aria-label="Not planned" />;
  const look = cellLook(cell.status);
  const struck = look.icon === "cancelled";
  return (
    <Tooltip title={hint(cell)} placement="top" enterDelay={400}>
      <ButtonBase
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
        aria-label={`Email ${cell.email_no}: ${hint(cell)}`}
        sx={{ justifyContent: "flex-start", gap: 0.625, px: 1, py: 0.5, ml: -1, borderRadius: 1.5,
          color: TONE_COLOR[look.tone], "&:hover": { bgcolor: "rgba(255,255,255,0.05)" } }}
      >
        {look.icon === "sent" && <CheckCircleRounded sx={{ fontSize: 17 }} />}
        {look.icon === "today" && <ScheduleSendRounded sx={{ fontSize: 17 }} />}
        <Typography component="span" noWrap sx={{ fontSize: 13, fontWeight: look.tone === "muted" ? 500 : 600,
          textDecoration: struck ? "line-through" : "none", opacity: struck ? 0.75 : 1 }}>
          {label(cell, tz)}
        </Typography>
        {cell.late && (
          <Stack direction="row" alignItems="center" sx={{ color: "warning.main", ml: 0.25 }}>
            <WarningAmberRounded sx={{ fontSize: 15 }} />
            <Box component="span" sx={{ fontSize: 11.5, fontWeight: 700, ml: 0.25 }}>late</Box>
          </Stack>
        )}
      </ButtonBase>
    </Tooltip>
  );
}
