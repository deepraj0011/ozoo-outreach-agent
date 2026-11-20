import { useEffect, useState } from "react";
import { Alert, Box, Button, Card, Drawer, IconButton, MenuItem, Stack, TextField, Tooltip,
  Typography } from "@mui/material";
import AddRounded from "@mui/icons-material/AddRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import { errorText, postJson } from "../api";
import { calendarDay, timeOfDay } from "../format";
import type { Board, ExampleDate, ScheduleRow } from "../types";
import { useUser } from "../user";

const MAX_EMAILS = 10;
const DEFAULT_GAP_DAYS = 3;
const EXAMPLE_DELAY_MS = 350;
const DRAWER_WIDTH = 460;

type Mode = ScheduleRow["mode"];

interface Draft {
  template_id: number | "";
  mode: Mode;
  day: string;
  at: string;
}

const MODES: { value: Mode; label: string }[] = [
  { value: "now", label: "Right away (email 1 only)" },
  { value: "day", label: "On a day" },
  { value: "at", label: "Date and time" },
];

const toDraft = (r: ScheduleRow): Draft => ({ template_id: r.template_id, mode: r.mode || "day", day: String(r.day),
  at: r.at ?? "" });

/** A row as the API wants it; unreadable values go as they are so the API can name the problem. */
const toApi = (r: Draft) => ({
  template_id: r.template_id === "" ? null : r.template_id,
  mode: r.mode,
  day: /^\d+$/.test(r.day.trim()) ? Number(r.day) : r.day,
  at: r.mode === "at" ? r.at : null,
});

function newRow(rows: Draft[], board: Board): Draft {
  const last = rows[rows.length - 1];
  const firstLive = board.templates.find((t) => t.status === "live");
  const lastDay = last && /^\d+$/.test(last.day.trim()) ? Number(last.day) : -DEFAULT_GAP_DAYS;
  return { template_id: last?.template_id ?? firstLive?.id ?? "", mode: "day", day: String(lastDay + DEFAULT_GAP_DAYS),
    at: "" };
}

function exampleText(ex: ExampleDate | undefined, row: Draft, tz: string): string {
  if (!ex || !ex.date) return row.mode === "at" ? "Pick a date and time" : row.mode === "day" ? "Enter a day, 0 or more" : "";
  const day = calendarDay(ex.date);
  if (ex.exact && ex.when) return `A lead added now gets it ${day}, ${timeOfDay(ex.when, tz)}`;
  return `A lead added now gets it around ${day}`;
}

interface RowProps {
  i: number;
  row: Draft;
  board: Board;
  example: ExampleDate | undefined;
  canRemove: boolean;
  onChange: (patch: Partial<Draft>) => void;
  onRemove: () => void;
}

function RowCard({ i, row, board, example, canRemove, onChange, onRemove }: RowProps) {
  const live = board.templates.filter((t) => t.status === "live");
  const current = board.templates.find((t) => t.id === row.template_id);
  const setMode = (mode: Mode) => {
    const at = mode === "at" && !row.at && example?.date ? `${example.date}T10:00` : row.at;
    onChange({ mode, at });
  };
  return (
    <Card sx={{ p: 2, bgcolor: "#202328", flexShrink: 0 }}>
      <Stack direction="row" alignItems="center" sx={{ mb: 1.5 }}>
        <Box sx={{ width: 26, height: 26, borderRadius: "50%", display: "grid", placeItems: "center", mr: 1.25,
          bgcolor: "#1d2e2c", color: "primary.main", fontWeight: 700, fontSize: 12.5 }}>{i + 1}</Box>
        <Typography sx={{ fontWeight: 700, fontSize: 14, flexGrow: 1 }}>Email {i + 1}</Typography>
        <Tooltip title={canRemove ? "Remove this email" : "The schedule needs at least one email"}>
          <span>
            <IconButton size="small" onClick={onRemove} disabled={!canRemove} aria-label={`Remove email ${i + 1}`}>
              <DeleteOutlineRounded fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>
      <Stack spacing={1.5}>
        <TextField select fullWidth size="small" label="Template" value={row.template_id}
          onChange={(e) => onChange({ template_id: Number(e.target.value) })}>
          {live.map((t) => <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>)}
          {current && current.status !== "live" && (
            <MenuItem value={current.id} disabled>{current.name} ({current.status})</MenuItem>
          )}
        </TextField>
        <Stack direction="row" spacing={1.5}>
          <TextField select size="small" label="Timing" value={row.mode} sx={{ flex: 1 }}
            onChange={(e) => setMode(e.target.value as Mode)}>
            {MODES.map((m) => (
              <MenuItem key={m.value} value={m.value} disabled={m.value === "now" && i > 0}>{m.label}</MenuItem>
            ))}
          </TextField>
          {row.mode === "day" && (
            <TextField size="small" type="number" label="Day" value={row.day} sx={{ width: 110 }}
              inputProps={{ min: 0, step: 1 }} onChange={(e) => onChange({ day: e.target.value })} />
          )}
        </Stack>
        {row.mode === "at" && (
          <TextField size="small" type="datetime-local" label="Date and time" value={row.at}
            InputLabelProps={{ shrink: true }} onChange={(e) => onChange({ at: e.target.value })} />
        )}
        <Typography variant="caption" color="text.secondary" sx={{ minHeight: 18 }}>
          {exampleText(example, row, board.tz)}
        </Typography>
      </Stack>
    </Card>
  );
}

interface Props {
  open: boolean;
  addRow: boolean;
  board: Board;
  onClose: () => void;
  onSaved: (message: string) => void;
}

export default function ScheduleDrawer({ open, addRow, board, onClose, onSaved }: Props) {
  const { name } = useUser();
  const [rows, setRows] = useState<Draft[]>([]);
  const [example, setExample] = useState<ExampleDate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Fresh draft each time the drawer opens; the board is not re-read while it is open.
  useEffect(() => {
    if (!open) return;
    const base = board.schedule.map(toDraft);
    setRows(addRow && base.length < MAX_EMAILS ? [...base, newRow(base, board)] : base);
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
    <Drawer anchor="right" open={open} onClose={busy ? undefined : onClose}
      PaperProps={{ sx: { width: { xs: "100%", sm: DRAWER_WIDTH }, borderRadius: 0 } }}>
      <Stack sx={{ height: "100%" }}>
        <Stack direction="row" alignItems="flex-start" sx={{ px: 2.5, pt: 2.5, pb: 2, borderBottom: 1, borderColor: "divider" }}>
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h6" sx={{ fontSize: 17 }}>Edit email schedule</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Day 0 is the day a lead starts. Day emails go in working hours; a date and time goes exactly then.
            </Typography>
          </Box>
          <IconButton onClick={onClose} disabled={busy} aria-label="Close"><CloseRounded /></IconButton>
        </Stack>
        <Stack spacing={1.5} sx={{ p: 2.5, flexGrow: 1, overflowY: "auto" }}>
          {rows.map((r, i) => (
            <RowCard key={i} i={i} row={r} board={board} example={example[i]} canRemove={rows.length > 1}
              onChange={(p) => update(i, p)} onRemove={() => change(rows.filter((_, j) => j !== i))} />
          ))}
          <Button variant="outlined" startIcon={<AddRounded />} disabled={rows.length >= MAX_EMAILS}
            onClick={() => change([...rows, newRow(rows, board)])} sx={{ borderStyle: "dashed", py: 1.25, flexShrink: 0 }}>
            Add email
          </Button>
        </Stack>
        <Box sx={{ px: 2.5, py: 2, borderTop: 1, borderColor: "divider" }}>
          {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button onClick={onClose} disabled={busy}>Cancel</Button>
            <Button variant="contained" onClick={save} disabled={busy || rows.length === 0}>Save schedule</Button>
          </Stack>
        </Box>
      </Stack>
    </Drawer>
  );
}
