import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Card, IconButton, MenuItem, Stack, TextField, Tooltip, Typography } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import CloseIcon from "@mui/icons-material/Close";
import { errorText, postJson } from "../api";
import { calendarDay } from "../format";
import type { Board, ExampleDate } from "../types";
import { useUser } from "../user";

const MAX_EMAILS = 10;
const EXAMPLE_DELAY_MS = 400;

interface Draft {
  template_id: number | "";
  day: string;
}

function fromBoard(board: Board): Draft[] {
  return board.schedule.map((s) => ({ template_id: s.template_id, day: String(s.day) }));
}

function payload(rows: Draft[]) {
  return rows.map((r) => ({ template_id: r.template_id === "" ? null : r.template_id,
    day: r.day.trim() === "" ? null : Number(r.day) }));
}

interface Props {
  board: Board;
  onSaved: (message: string) => void;
  onDirtyChange: (dirty: boolean) => void;
}

export default function ScheduleStrip({ board, onSaved, onDirtyChange }: Props) {
  const { name } = useUser();
  const saved = useMemo(() => fromBoard(board), [board]);
  const [rows, setRows] = useState<Draft[]>(saved);
  const [dirty, setDirty] = useState(false);
  const [example, setExample] = useState<ExampleDate[]>(board.example);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!dirty) {
      setRows(saved);
      setExample(board.example);
    }
  }, [saved, board.example, dirty]);

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);

  useEffect(() => {
    if (!dirty) return undefined;
    const t = window.setTimeout(() => {
      postJson<{ example: ExampleDate[] }>("/demo/api/p0/schedule/example", { rows: payload(rows) })
        .then((r) => setExample(r.example))
        .catch(() => undefined);
    }, EXAMPLE_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [rows, dirty]);

  const live = board.templates.filter((t) => t.status === "live");
  const change = (next: Draft[]) => {
    setRows(next);
    setDirty(true);
    setError(null);
  };
  const update = (i: number, patch: Partial<Draft>) => change(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const remove = (i: number) => change(rows.filter((_, j) => j !== i));
  const add = () => {
    const last = rows[rows.length - 1];
    const day = last && last.day.trim() !== "" ? String(Number(last.day) + 5) : "0";
    change([...rows, { template_id: live[0]?.id ?? "", day }]);
  };
  const cancel = () => {
    setRows(saved);
    setExample(board.example);
    setDirty(false);
    setError(null);
  };
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await postJson("/demo/api/p0/schedule", { rows: payload(rows), by: name });
      setDirty(false);
      onSaved("Schedule saved. Every lead still on the schedule now follows it.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box>
      <Stack direction="row" alignItems="baseline" justifyContent="space-between" sx={{ mb: 1 }}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Email schedule</Typography>
          <Typography variant="caption" color="text.secondary">
            Which template goes as which email, and on which day after the lead starts (day 0).
          </Typography>
        </Box>
        {dirty && (
          <Stack direction="row" spacing={1}>
            <Button onClick={cancel} disabled={busy}>Cancel</Button>
            <Button variant="contained" onClick={save} disabled={busy}>Save schedule</Button>
          </Stack>
        )}
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError(null)}>{error}</Alert>}
      <Stack direction="row" spacing={1.5} sx={{ overflowX: "auto", pb: 1 }}>
        {rows.map((r, i) => {
          const date = example[i]?.date ? calendarDay(example[i].date) : "";
          const known = live.some((t) => t.id === r.template_id);
          const other = board.templates.find((t) => t.id === r.template_id);
          return (
            <Card key={i} sx={{ minWidth: 220, maxWidth: 240, p: 1.5, flexShrink: 0,
              borderColor: dirty ? "primary.light" : "divider" }}>
              <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
                <Typography sx={{ fontWeight: 700, fontSize: 14 }}>Email {i + 1}</Typography>
                <Tooltip title={rows.length > 1 ? "Remove this email" : "The schedule needs at least one email"}>
                  <span>
                    <IconButton size="small" onClick={() => remove(i)} disabled={rows.length <= 1}
                      aria-label={`Remove email ${i + 1}`}>
                      <CloseIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              </Stack>
              <TextField select fullWidth size="small" label="Template" value={r.template_id}
                onChange={(e) => update(i, { template_id: Number(e.target.value) })}>
                {live.map((t) => <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>)}
                {!known && other && <MenuItem value={other.id} disabled>{other.name} ({other.status})</MenuItem>}
              </TextField>
              <TextField fullWidth size="small" type="number" label="Day" value={r.day} sx={{ mt: 1.5 }}
                inputProps={{ min: 0, step: 1 }} onChange={(e) => update(i, { day: e.target.value })} />
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, minHeight: 18 }}>
                {date ? `A lead starting today: ~${date}` : " "}
              </Typography>
            </Card>
          );
        })}
        <Button variant="outlined" onClick={add} disabled={rows.length >= MAX_EMAILS || live.length === 0}
          startIcon={<AddIcon />}
          sx={{ minWidth: 150, flexShrink: 0, borderStyle: "dashed", borderRadius: 2.5 }}>
          Add email
        </Button>
      </Stack>
    </Box>
  );
}
