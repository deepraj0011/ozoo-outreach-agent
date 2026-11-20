import { Box, Button, ButtonBase, Card, Stack, Typography } from "@mui/material";
import AddRounded from "@mui/icons-material/AddRounded";
import BoltRounded from "@mui/icons-material/BoltRounded";
import CalendarTodayRounded from "@mui/icons-material/CalendarTodayRounded";
import EditRounded from "@mui/icons-material/EditRounded";
import EventRounded from "@mui/icons-material/EventRounded";
import ScheduleRounded from "@mui/icons-material/ScheduleRounded";
import type { ScheduleRow } from "../types";
import { stripItems, timingLabel } from "../lib/campaign";

const MAX_EMAILS = 10;
const STEP_MIN_WIDTH = 150;
const ADD_WIDTH = 132;
const DOT = 30;

const TIMING_ICON: Record<ScheduleRow["mode"], typeof BoltRounded> = {
  now: BoltRounded,
  at: EventRounded,
  day: CalendarTodayRounded,
};

interface Props {
  schedule: ScheduleRow[];
  tz: string;
  onEdit: (addRow: boolean) => void;
}

function Step({ row, name, first, last, tz }: { row: ScheduleRow; name: string; first: boolean; last: boolean;
  tz: string }) {
  const Icon = TIMING_ICON[row.mode] ?? CalendarTodayRounded;
  const title = name || `Email ${row.email_no}`;
  return (
    <Box sx={{ position: "relative", pr: 2, minWidth: STEP_MIN_WIDTH,
      "&::after": last ? undefined : { content: '""', position: "absolute", top: DOT / 2 - 1, left: DOT + 6,
        right: 6, height: 2, bgcolor: "divider", borderRadius: 1 } }}>
      <Box sx={{ position: "relative", zIndex: 1, width: DOT, height: DOT, borderRadius: "50%", display: "grid",
        placeItems: "center", fontWeight: 700, fontSize: 13,
        bgcolor: first ? "primary.main" : "#1d2e2c", color: first ? "primary.contrastText" : "primary.main" }}>
        {row.email_no}
      </Box>
      <Typography title={title} sx={{ mt: 1.25, fontWeight: 700, fontSize: 13.5 }} noWrap>{title}</Typography>
      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mt: 0.25, color: "text.secondary" }}>
        <Icon sx={{ fontSize: 15 }} />
        <Typography sx={{ fontSize: 12.5 }} noWrap>{timingLabel(row, tz)}</Typography>
      </Stack>
    </Box>
  );
}

export default function ScheduleStrip({ schedule, tz, onEdit }: Props) {
  const steps = stripItems(schedule);
  const n = steps.length;
  return (
    <Card>
      <Stack direction="row" alignItems="center" spacing={1.25}
        sx={{ px: 2, py: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <ScheduleRounded sx={{ color: "primary.main", fontSize: 20 }} />
        <Typography sx={{ fontWeight: 700, fontSize: 14.5 }}>Email schedule</Typography>
        <Typography color="text.secondary" sx={{ fontSize: 12.5, display: { xs: "none", sm: "block" } }}>
          {n} {n === 1 ? "email" : "emails"}, sent in working hours
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Button size="small" startIcon={<EditRounded />} onClick={() => onEdit(false)}>Edit</Button>
      </Stack>
      <Box sx={{ display: "grid", gap: 0, p: 2, overflowX: "auto",
        gridTemplateColumns: n ? `repeat(${n}, minmax(${STEP_MIN_WIDTH}px, 1fr)) ${ADD_WIDTH}px` : `${ADD_WIDTH}px` }}>
        {steps.map((s, i) => (
          <Step key={s.emailNo} row={s.row} name={s.name} first={i === 0} last={i === n - 1} tz={tz} />
        ))}
        {n < MAX_EMAILS && (
          <ButtonBase onClick={() => onEdit(true)} aria-label="Add email"
            sx={{ border: "1.5px dashed", borderColor: "divider", borderRadius: 2, color: "primary.main", minHeight: 72,
              fontWeight: 600, fontSize: 13.5, gap: 0.5, "&:hover": { borderColor: "primary.main", bgcolor: "#1d2e2c" } }}>
            <AddRounded sx={{ fontSize: 18 }} /> Add email
          </ButtonBase>
        )}
      </Box>
    </Card>
  );
}
