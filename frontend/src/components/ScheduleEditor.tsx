import { useEffect, useState } from "react";
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, MenuItem, Stack,
  TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import AddRounded from "@mui/icons-material/AddRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import { errorText, postJson } from "../api";
import { calendarDay, timeOfDay } from "../format";
import { businessDate, gapLabel, MAX_EMAILS, rowOffset, weekendNote } from "../lib/schedule";
import type { Board, ExampleDate, ScheduleRow } from "../types";
import { useUser } from "../user";
import { Marker } from "./ScheduleRuler";

/** C · Edit as a list: one row per email, the real date a lead added today gets it, Save replaces the schedule. */

const DEFAULT_GAP_DAYS = 3;
const EXAMPLE_DELAY_MS = 350;
const GAP_BAR_MAX_DAYS = 14;
const GAP_BAR_W = 64;
const COLUMNS = "36px minmax(170px, 1.3fr) 178px 196px 120px minmax(170px, 1fr) 40px";
const MIN_ROW_W = 1000;

type Mode = ScheduleRow["mode"];

interface Draft {
  template_id: number | "";
  mode: Mode;
  day: string;
  at: string;
}

const toDraft = (r: ScheduleRow): Draft => ({ template_id: r.template_id, mode: r.mode || "day", day: String(r.day),
  at: r.at ?? "" });

/** A row as the API wants it; unreadable values go as they are so the API can name the problem. */
const toApi = (r: Draft) => ({
  template_id: r.template_id === "" ? null : r.template_id,
  mode: r.mode,
  day: /^\d+$/.test(r.day.trim()) ? Number(r.day) : r.day,
  at: r.mode === "at" ? r.at : null,
});

function newRow(rows: Draft[], board: Board, today: string): Draft {
  const last = rows[rows.length - 1];
  const firstLive = board.templates.find((t) => t.status === "live");
  const lastDay = last ? rowOffset(last, today) : null;
  return { template_id: last?.template_id ?? firstLive?.id ?? "", mode: "day",
    day: String(lastDay === null ? 0 : lastDay + DEFAULT_GAP_DAYS), at: "" };
}

function dateText(ex: ExampleDate | undefined, row: Draft, tz: string): string {
  if (!ex || !ex.date) return row.mode === "at" ? "Pick a date and time" : row.mode === "day" ? "Enter a day, 0 or more" : "";
  const day = calendarDay(ex.date);
  return ex.exact && ex.when ? `${day}, ${timeOfDay(ex.when, tz)}` : day;
}

function GapBar({ prev, cur }: { prev: number | null; cur: number | null }) {
  const label = gapLabel(prev, cur);
  if (!label) return <Box />;
  const gap = Math.max(0, (cur ?? 0) - (prev ?? 0));
  const w = Math.max(4, Math.min(1, gap / GAP_BAR_MAX_DAYS) * GAP_BAR_W);
  return (
    <Stack spacing={0.5} aria-label={`${label} since the previous email`}>
      <Box sx={{ width: GAP_BAR_W, height: 4, borderRadius: 2, bgcolor: "divider" }}>
        <Box sx={{ width: w, height: 4, borderRadius: 2, bgcolor: "primary.main" }} />
      </Box>
      <Typography sx={{ fontSize: 12, color: "text.secondary" }}>{label}</Typography>
    </Stack>
  );
}

interface RowProps {
  i: number;
  row: Draft;
  board: Board;
  today: string;
  prevOffset: number | null;
  example: ExampleDate | undefined;
  canRemove: boolean;
  onChange: (patch: Partial<Draft>) => void;
  onRemove: () => void;
}

function EmailRow({ i, row, board, today, prevOffset, example, canRemove, onChange, onRemove }: RowProps) {
  const live = board.templates.filter((t) => t.status === "live");
  const current = board.templates.find((t) => t.id === row.template_id);
  const n = i + 1;
  const setMode = (mode: Mode | null) => {
    if (!mode) return;
    const at = mode === "at" && !row.at && example?.date ? `${example.date}T10:00` : row.at;
    onChange({ mode, at });
  };
  const note = weekendNote(row, example, today);
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: COLUMNS, gap: 1.5, alignItems: "center", py: 1.25, px: 1,
      borderBottom: 1, borderColor: "divider" }}>
      <Marker kind={row.mode} n={n} />
      <TextField select fullWidth size="small" label="Template" value={row.template_id} id={`email-${n}-template`}
        name={`email-${n}-template`}
        onChange={(e) => onChange({ template_id: Number(e.target.value) })} inputProps={{ "aria-label": `Email ${n} template` }}>
        {live.map((t) => <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>)}
        {current && current.status !== "live" && (
          <MenuItem value={current.id} disabled>{current.name} ({current.status})</MenuItem>
        )}
      </TextField>
      <ToggleButtonGroup exclusive size="small" value={row.mode} onChange={(_, v: Mode | null) => setMode(v)}
        aria-label={`Email ${n} timing`} sx={{ "& .MuiToggleButton-root": { px: 1.25, py: 0.5, textTransform: "none", fontSize: 13 } }}>
        {/* ToggleButtonGroup passes selection to direct children only, so no Tooltip wrapper here */}
        <ToggleButton value="now" disabled={i > 0} aria-label={i > 0 ? "Now (email 1 only)" : "Now"}>Now</ToggleButton>
        <ToggleButton value="day" aria-label="Day">Day</ToggleButton>
        <ToggleButton value="at" aria-label="Date">Date</ToggleButton>
      </ToggleButtonGroup>
      {row.mode === "day" && (
        <TextField size="small" type="number" label="Day" value={row.day} id={`email-${n}-day`} name={`email-${n}-day`}
          inputProps={{ min: 0, step: 1, "aria-label": `Email ${n} day` }} onChange={(e) => onChange({ day: e.target.value })} />
      )}
      {row.mode === "at" && (
        <TextField size="small" type="datetime-local" label="Date and time" value={row.at} id={`email-${n}-at`}
          name={`email-${n}-at`}
          InputLabelProps={{ shrink: true }} inputProps={{ "aria-label": `Email ${n} date and time` }}
          onChange={(e) => onChange({ at: e.target.value })} />
      )}
      {row.mode === "now" && <Typography sx={{ fontSize: 13, color: "text.secondary" }}>Day 0, right away</Typography>}
      <GapBar prev={i === 0 ? null : prevOffset} cur={rowOffset(row, today)} />
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ fontSize: 13, fontWeight: 600 }} noWrap>{dateText(example, row, board.tz)}</Typography>
        {note && <Typography sx={{ fontSize: 11.5, color: row.mode === "at" ? "warning.main" : "text.secondary" }} noWrap
          title={note}>{note}</Typography>}
      </Box>
      <Tooltip title={canRemove ? `Remove email ${n}` : "The schedule needs at least one email"}>
        <span>
          <IconButton size="small" onClick={onRemove} disabled={!canRemove} aria-label={`Remove email ${n}`}>
            <DeleteOutlineRounded fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
    </Box>
  );
}

function HeaderRow() {
  const cols = ["#", "Template", "Timing", "When", "Gap", "A lead added today gets it", ""];
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: COLUMNS, gap: 1.5, px: 1, pb: 1, borderBottom: 1, borderColor: "divider" }}>
      {cols.map((c, i) => (
        <Typography key={i} sx={{ fontSize: 11.5, fontWeight: 700, color: "text.secondary", textTransform: "uppercase",
          letterSpacing: 0.4 }}>{c}</Typography>
      ))}
    </Box>
  );
}

interface Props {
  open: boolean;
  board: Board;
  onClose: () => void;
  onSaved: (message: string) => void;
}

export default function ScheduleEditor({ open, board, onClose, onSaved }: Props) {
  const { name } = useUser();
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("md"));
  const today = businessDate(board.now, board.tz);
  const [rows, setRows] = useState<Draft[]>([]);
  const [example, setExample] = useState<ExampleDate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Fresh draft each time the editor opens; the board is not re-read while it is open.
  useEffect(() => {
    if (!open) return;
    setRows(board.schedule.map(toDraft));
    setExample(board.example);
    setError(null);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps -- only on open: a refresh must not drop edits

  useEffect(() => {
    if (!open || rows.length === 0) return undefined;
    let live = true;
    const t = window.setTimeout(() => {
      postJson<{ example: ExampleDate[] }>("/demo/api/p0/schedule/example", { rows: rows.map(toApi) })
        .then((r) => live && setExample(r.example))
        .catch(() => undefined); // the example is a hint only; Save reports real problems
    }, EXAMPLE_DELAY_MS);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [rows, open]);

  const change = (next: Draft[]) => {
    setRows(next);
    setError(null);
  };
  const update = (i: number, patch: Partial<Draft>) => change(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const offsets = rows.map((r) => rowOffset(r, today));

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/demo/api/p0/schedule", { rows: rows.map(toApi), by: name });
      onSaved("Schedule saved. Leads still waiting follow the new timing.");
      onClose();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="lg" fullScreen={narrow}
      aria-labelledby="schedule-editor-title">
      <DialogTitle id="schedule-editor-title" sx={{ display: "flex", alignItems: "flex-start", gap: 1 }}>
        <Box sx={{ flexGrow: 1 }}>
          <Typography variant="h6" component="span" sx={{ fontSize: 17 }}>Edit email schedule</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Day 0 is the day a lead starts. Day emails go in working hours; a date and time goes exactly then.
            Up to {MAX_EMAILS} emails.
          </Typography>
        </Box>
        <IconButton onClick={onClose} disabled={busy} aria-label="Close"><CloseRounded /></IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ px: 2 }}>
        <Box sx={{ overflowX: "auto" }}>
          <Box sx={{ minWidth: MIN_ROW_W }}>
            <HeaderRow />
            {rows.map((r, i) => (
              <EmailRow key={i} i={i} row={r} board={board} today={today} prevOffset={i > 0 ? offsets[i - 1] : null}
                example={example[i]} canRemove={rows.length > 1}
                onChange={(p) => update(i, p)} onRemove={() => change(rows.filter((_, j) => j !== i))} />
            ))}
          </Box>
        </Box>
        <Button variant="outlined" startIcon={<AddRounded />} disabled={rows.length >= MAX_EMAILS}
          onClick={() => change([...rows, newRow(rows, board, today)])} sx={{ mt: 1.5, borderStyle: "dashed" }}>
          {rows.length >= MAX_EMAILS ? `${MAX_EMAILS} emails is the most` : `Add email ${rows.length + 1}`}
        </Button>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2, flexDirection: "column", alignItems: "stretch", gap: 1.5 }}>
        {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
        <Stack direction="row" spacing={1} justifyContent="flex-end">
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="contained" onClick={save} disabled={busy || rows.length === 0}>Save schedule</Button>
        </Stack>
      </DialogActions>
    </Dialog>
  );
}
