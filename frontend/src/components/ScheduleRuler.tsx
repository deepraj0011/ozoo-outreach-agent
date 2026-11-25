import { Box, Button, Card, Chip, Stack, Tooltip, Typography } from "@mui/material";
import EditRounded from "@mui/icons-material/EditRounded";
import ScheduleRounded from "@mui/icons-material/ScheduleRounded";
import type { Board, ScheduleRow } from "../types";
import { businessDate, rulerLayout, weekBands, type RulerMark } from "../lib/schedule";
import { TONE_SX } from "./status";

/** A · Day ruler: every email on one horizontal day axis, placed by the day it goes for a lead added today. */

const MARK = 24;
const LABEL_W = 100;
const LABEL_ZONE = 64;
const AXIS_ZONE = 40;
const SIDE_PAD = LABEL_W / 2 + 8;
const PX_PER_EMAIL = 56; // > LABEL_W / 2: labels alternate above and below, so same-side ones never touch
const WEEK_STRIP = 18;
const MAX_GAP_PCT = 8;
const MIN_TRACK = 640;
const TEAL = "#2dd4bf";
const AMBER = "#fbbf24";

const MARK_SX: Record<ScheduleRow["mode"], object> = {
  now: { bgcolor: TEAL, color: "#0f1412", borderRadius: "50%", border: `2px solid ${TEAL}` },
  day: { bgcolor: "background.paper", color: TEAL, borderRadius: "50%", border: `2px solid ${TEAL}` },
  at: { bgcolor: "#3a2f12", color: AMBER, borderRadius: "6px", border: `2px solid ${AMBER}` },
};

export function Marker({ kind, n, size = MARK }: { kind: ScheduleRow["mode"]; n: number | string; size?: number }) {
  return (
    <Box sx={{ width: size, height: size, display: "grid", placeItems: "center", fontWeight: 700,
      fontSize: size > 20 ? 12 : 10.5, boxSizing: "border-box", lineHeight: 1, ...MARK_SX[kind] }}>{n}</Box>
  );
}

function Label({ m }: { m: RulerMark }) {
  const name = m.name || `Email ${m.emailNo}`;
  return (
    <Box sx={{ position: "absolute", left: `${m.pct}%`, width: LABEL_W, transform: "translateX(-50%)",
      textAlign: "center", ...(m.above ? { bottom: WEEK_STRIP + LABEL_ZONE + AXIS_ZONE / 2 + MARK / 2 + 4 } :
        { top: LABEL_ZONE + AXIS_ZONE / 2 + MARK / 2 + 4 }) }}>
      <Typography sx={{ fontSize: 11.5, fontWeight: 700, color: m.kind === "at" ? AMBER : "primary.main" }} noWrap>
        {m.when}
      </Typography>
      <Typography title={name} sx={{ fontSize: 12, fontWeight: 600 }} noWrap>{name}</Typography>
      <Typography sx={{ fontSize: 11.5, color: "text.secondary" }} noWrap>{m.date}</Typography>
    </Box>
  );
}

function Mark({ m }: { m: RulerMark }) {
  const stem = m.above ? { bottom: "50%", height: AXIS_ZONE / 2 + 2 } : { top: "50%", height: AXIS_ZONE / 2 + 2 };
  return (
    <>
      <Box sx={{ position: "absolute", left: `${m.pct}%`, top: LABEL_ZONE, height: AXIS_ZONE, width: 0 }}>
        <Box sx={{ position: "absolute", left: -0.5, width: "1px", bgcolor: "divider", ...stem }} />
        <Tooltip title={`Email ${m.emailNo} · ${m.when} · ${m.name}${m.date ? ` · ${m.date}` : ""}`}>
          <Box sx={{ position: "absolute", top: "50%", left: 0, transform: "translate(-50%, -50%)", zIndex: 1 }}>
            <Marker kind={m.kind} n={m.emailNo} />
          </Box>
        </Tooltip>
      </Box>
      <Label m={m} />
    </>
  );
}

function Legend() {
  const items: { kind: ScheduleRow["mode"]; text: string }[] = [
    { kind: "now", text: "Right away (day 0)" },
    { kind: "day", text: "Day N after a lead starts" },
    { kind: "at", text: "Fixed date and time" },
  ];
  return (
    <Stack direction="row" spacing={2.5} useFlexGap flexWrap="wrap" alignItems="center"
      sx={{ px: 2, py: 1.25, borderTop: 1, borderColor: "divider" }}>
      {items.map((i) => (
        <Stack key={i.kind} direction="row" spacing={0.75} alignItems="center">
          <Marker kind={i.kind} n="" size={14} />
          <Typography sx={{ fontSize: 12, color: "text.secondary" }}>{i.text}</Typography>
        </Stack>
      ))}
      <Box sx={{ flexGrow: 1 }} />
      <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
        Dates are for a lead added today; Day emails go in working hours.
      </Typography>
    </Stack>
  );
}

export default function ScheduleRuler({ board, onEdit }: { board: Board; onEdit: () => void }) {
  const today = businessDate(board.now, board.tz);
  const n = board.schedule.length;
  // neighbours at least half a label apart at the narrowest track, so same-side labels never overlap
  const layout = rulerLayout(board.schedule, board.example, today, board.tz, n > 1 ? Math.min(100 / n, MAX_GAP_PCT) : undefined);
  const trackMin = Math.max(MIN_TRACK, n * PX_PER_EMAIL);
  return (
    <Card>
      <Stack direction="row" alignItems="center" spacing={1.25} useFlexGap flexWrap="wrap"
        sx={{ px: 2, py: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <ScheduleRounded sx={{ color: "primary.main", fontSize: 20 }} />
        <Typography sx={{ fontWeight: 700, fontSize: 14.5 }}>Email schedule</Typography>
        <Chip size="small" label={`${n} ${n === 1 ? "email" : "emails"}`} sx={{ ...TONE_SX.accent, fontWeight: 600 }} />
        {n > 0 && (
          <Chip size="small" sx={{ ...TONE_SX.muted, fontWeight: 600 }}
            label={`${layout.lastDay + 1} ${layout.lastDay === 0 ? "day" : "days"}${layout.endDate ? `, ends ${layout.endDate}` : ""}`} />
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Button size="small" startIcon={<EditRounded />} onClick={onEdit}>Edit</Button>
      </Stack>
      <Box sx={{ overflowX: "auto" }}>
        <Box sx={{ minWidth: trackMin + 2 * SIDE_PAD, px: `${SIDE_PAD}px`, py: 1.5 }}>
          <Box role="img" aria-label={`Email schedule: ${layout.marks.map((m) => `email ${m.emailNo} ${m.when}`).join(", ")}`}
            sx={{ position: "relative", height: 2 * LABEL_ZONE + AXIS_ZONE + WEEK_STRIP }}>
            {weekBands(layout.span).map((b) => (
              <Box key={b.week} sx={{ position: "absolute", top: 0, bottom: 0, left: `${b.from}%`, width: `${b.to - b.from}%`,
                bgcolor: b.shaded ? "rgba(255,255,255,0.03)" : "transparent", borderLeft: 1, borderColor: "divider" }}>
                <Typography sx={{ position: "absolute", bottom: 2, left: 6, fontSize: 10.5, color: "text.disabled",
                  textTransform: "uppercase", letterSpacing: 0.5 }}>Week {b.week}</Typography>
              </Box>
            ))}
            <Box sx={{ position: "absolute", left: 0, right: 0, top: LABEL_ZONE + AXIS_ZONE / 2 - 1, height: 2,
              bgcolor: "divider", borderRadius: 1 }} />
            {layout.marks.map((m) => <Mark key={m.emailNo} m={m} />)}
          </Box>
        </Box>
      </Box>
      <Legend />
    </Card>
  );
}
