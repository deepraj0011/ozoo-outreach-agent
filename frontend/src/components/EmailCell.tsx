import { Box, ButtonBase, Stack, Tooltip, Typography } from "@mui/material";
import CheckIcon from "@mui/icons-material/CheckCircle";
import OpenIcon from "@mui/icons-material/DraftsOutlined";
import ClickIcon from "@mui/icons-material/AdsClickOutlined";
import ReadIcon from "@mui/icons-material/AutoStoriesOutlined";
import ScheduleIcon from "@mui/icons-material/ScheduleOutlined";
import WarningIcon from "@mui/icons-material/WarningAmberRounded";
import type { ReactNode } from "react";
import type { Cell } from "../types";
import { shortDate, timeOfDay } from "../format";

function Count({ icon, n, title }: { icon: ReactNode; n: number; title: string }) {
  if (!n) return null;
  return (
    <Tooltip title={title}>
      <Stack direction="row" alignItems="center" spacing={0.25} sx={{ color: "text.secondary", "& svg": { fontSize: 13 } }}>
        {icon}
        <Typography sx={{ fontSize: 11, fontWeight: 600 }}>{n}</Typography>
      </Stack>
    </Tooltip>
  );
}

function Body({ cell, tz }: { cell: Cell; tz: string }) {
  const date = shortDate(cell.date, tz);
  switch (cell.status) {
    case "sent":
      return (
        <>
          <Stack direction="row" alignItems="center" spacing={0.5} sx={{ color: "success.main" }}>
            <CheckIcon sx={{ fontSize: 16 }} />
            <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{shortDate(cell.sent_at ?? cell.date, tz)}</Typography>
          </Stack>
          <Stack direction="row" spacing={1} sx={{ mt: 0.25, minHeight: 16 }}>
            <Count icon={<OpenIcon />} n={cell.opens} title="Opened (people, not scanners)" />
            <Count icon={<ClickIcon />} n={cell.clicks} title="Link clicks" />
            <Count icon={<ReadIcon />} n={cell.visits} title="Read the web page" />
            {!cell.opens && !cell.clicks && !cell.visits && (
              <Typography sx={{ fontSize: 11, color: "text.disabled" }}>no opens yet</Typography>
            )}
          </Stack>
        </>
      );
    case "today":
      return (
        <Stack direction="row" alignItems="center" spacing={0.5} sx={{ color: "info.main" }}>
          <ScheduleIcon sx={{ fontSize: 16 }} />
          <Typography sx={{ fontSize: 13, fontWeight: 600 }}>Today {timeOfDay(cell.date, tz)}</Typography>
        </Stack>
      );
    case "handed":
      return <Typography sx={{ fontSize: 13, fontWeight: 600, color: "info.main" }}>Sending {date}</Typography>;
    case "planned":
      return <Typography sx={{ fontSize: 13, color: "text.secondary" }}>{date}</Typography>;
    case "skipped":
      return <Typography sx={{ fontSize: 12, color: "text.disabled" }}>Skipped</Typography>;
    default:
      return <Typography sx={{ fontSize: 12, color: "text.disabled" }}>Cancelled</Typography>;
  }
}

export default function EmailCell({ cell, tz, onClick }: { cell: Cell | undefined; tz: string; onClick: () => void }) {
  if (!cell) return <Typography sx={{ fontSize: 13, color: "text.disabled", px: 1 }}>—</Typography>;
  const bg = cell.status === "sent" ? "rgba(21,128,61,0.06)" : cell.status === "today" ? "rgba(29,78,216,0.07)" : "transparent";
  return (
    <ButtonBase
      onClick={onClick}
      aria-label={`Email ${cell.email_no}: ${cell.status}`}
      sx={{ display: "block", textAlign: "left", width: "100%", px: 1, py: 0.75, borderRadius: 1.5, bgcolor: bg,
        "&:hover": { bgcolor: "rgba(55,48,163,0.07)" } }}
    >
      <Body cell={cell} tz={tz} />
      {cell.late && (
        <Stack direction="row" alignItems="center" spacing={0.25} sx={{ color: "warning.main", mt: 0.25 }}>
          <WarningIcon sx={{ fontSize: 13 }} />
          <Box component="span" sx={{ fontSize: 11, fontWeight: 700 }}>late</Box>
        </Stack>
      )}
    </ButtonBase>
  );
}
