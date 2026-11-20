import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Box, CircularProgress, InputAdornment, Snackbar, Stack, Tab, Tabs, TextField,
  Typography } from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import { errorText, getJson } from "../api";
import { dateTime } from "../format";
import type { Board, BoardLead } from "../types";
import BoardTable from "../components/BoardTable";
import EmailDrawer from "../components/EmailDrawer";
import LeadDrawer from "../components/LeadDrawer";
import ScheduleStrip from "../components/ScheduleStrip";
import TotalsRow from "../components/TotalsRow";

const REFRESH_MS = 15000;
type Filter = "all" | "today" | "replied" | "stopped";

function matches(lead: BoardLead, filter: Filter, query: string): boolean {
  if (filter === "today" && !lead.emails.some((e) => e.status === "today")) return false;
  if (filter === "replied" && lead.stop_reason !== "replied") return false;
  if (filter === "stopped" && lead.stop_reason === null) return false;
  if (!query) return true;
  const q = query.toLowerCase();
  return lead.name.toLowerCase().includes(q) || lead.company.toLowerCase().includes(q);
}

export default function CampaignPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [email, setEmail] = useState<{ leadId: number; leadName: string; emailNo: number } | null>(null);
  const [leadId, setLeadId] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setBoard(await getJson<Board>("/demo/api/p0/board"));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const busy = email !== null || leadId !== null || editing;
  useEffect(() => {
    if (busy) return undefined;
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [busy, load]);

  const leads = useMemo(() => (board ? board.leads.filter((l) => matches(l, filter, query.trim())) : []),
    [board, filter, query]);
  const lead = board?.leads.find((l) => l.id === leadId) ?? null;
  const onDirty = useCallback((d: boolean) => setEditing(d), []);
  const done = (message: string) => {
    setToast(message);
    void load();
  };
  const openEmail = (l: BoardLead, emailNo: number) => setEmail({ leadId: l.id, leadName: l.name, emailNo });

  if (!board) {
    return error ? <Alert severity="error">{error}</Alert> : (
      <Box sx={{ py: 10, textAlign: "center" }}><CircularProgress /></Box>
    );
  }
  const counts = {
    all: board.leads.length,
    today: board.leads.filter((l) => l.emails.some((e) => e.status === "today")).length,
    replied: board.totals.replied,
    stopped: board.totals.stopped,
  };
  return (
    <Stack spacing={3}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "flex-end" }} spacing={1}>
        <Box>
          <Typography variant="h5">Campaign</Typography>
          <Typography color="text.secondary" sx={{ fontSize: 14 }}>
            Every lead and every email: what went, what goes today, what comes next.
          </Typography>
        </Box>
        <Typography variant="caption" color="text.secondary">
          Updated {dateTime(board.now, board.tz)} · times in {board.tz}
        </Typography>
      </Stack>
      {error && <Alert severity="warning">Could not refresh: {error}</Alert>}
      <ScheduleStrip board={board} onSaved={done} onDirtyChange={onDirty} />
      <TotalsRow totals={board.totals} />
      <Box>
        <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }}
          spacing={1} sx={{ mb: 1.5 }}>
          <Tabs value={filter} onChange={(_, v) => setFilter(v)} variant="scrollable" allowScrollButtonsMobile>
            <Tab value="all" label={`All (${counts.all})`} />
            <Tab value="today" label={`Going today (${counts.today})`} />
            <Tab value="replied" label={`Replied (${counts.replied})`} />
            <Tab value="stopped" label={`Stopped (${counts.stopped})`} />
          </Tabs>
          <TextField size="small" placeholder="Search name or company" value={query}
            onChange={(e) => setQuery(e.target.value)} sx={{ minWidth: 260 }}
            InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> }} />
        </Stack>
        <BoardTable board={board} leads={leads} onCell={openEmail} onLead={(l) => setLeadId(l.id)} />
      </Box>
      <EmailDrawer target={email} tz={board.tz} onClose={() => setEmail(null)} />
      <LeadDrawer lead={email ? null : lead} tz={board.tz} onClose={() => setLeadId(null)} onChanged={done}
        onEmail={openEmail} />
      <Snackbar open={toast !== null} autoHideDuration={4000} onClose={() => setToast(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setToast(null)}>{toast}</Alert>
      </Snackbar>
    </Stack>
  );
}
