import { useCallback, useEffect, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Box, Button, Card, Chip, InputAdornment, Skeleton, Snackbar, Stack, Tab, Tabs, TextField,
  Typography } from "@mui/material";
import PauseCircleRounded from "@mui/icons-material/PauseCircleRounded";
import PauseRounded from "@mui/icons-material/PauseRounded";
import PersonAddRounded from "@mui/icons-material/PersonAddRounded";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import PlayCircleRounded from "@mui/icons-material/PlayCircleRounded";
import SearchRounded from "@mui/icons-material/SearchRounded";
import InboxRounded from "@mui/icons-material/InboxRounded";
import { errorText, getBoard, pauseCampaign, startCampaign } from "../api";
import { campaignHeaderState, inTab, matchesQuery, tabCounts, type HeaderState, type TabKey } from "../lib/campaign";
import { pageState } from "../lib/pageState";
import type { Board, BoardLead } from "../types";
import { useUser } from "../user";
import BoardTable from "../components/BoardTable";
import EmailDrawer from "../components/EmailDrawer";
import LeadDrawer from "../components/LeadDrawer";
import PageState from "../components/PageState";
import ScheduleDrawer from "../components/ScheduleDrawer";
import ScheduleStrip from "../components/ScheduleStrip";
import TotalsRow from "../components/TotalsRow";
import { TONE_SX } from "../components/status";

const REFRESH_MS = 15000;

const TABS: { key: TabKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "today", label: "Sending today" },
  { key: "replied", label: "Replied" },
  { key: "stopped", label: "Stopped" },
];

const STATE_LOOK: Record<HeaderState["label"], { sx: { bgcolor: string; color: string }; sending: boolean }> = {
  Sending: { sx: TONE_SX.success, sending: true },
  Paused: { sx: TONE_SX.warning, sending: false },
  "Not started": { sx: TONE_SX.muted, sending: false },
};

type Toast = { severity: "success" | "error"; text: string };

function Header({ board, busy, onToggle }: { board: Board; busy: boolean; onToggle: (s: HeaderState) => void }) {
  const st = campaignHeaderState(board.campaign);
  const look = STATE_LOOK[st.label];
  return (
    <Stack direction="row" alignItems="center" spacing={1.5} useFlexGap flexWrap="wrap">
      <Typography variant="h5">Campaign</Typography>
      <Chip size="small" label={st.label}
        icon={look.sending ? <PlayCircleRounded /> : <PauseCircleRounded />}
        sx={{ ...look.sx, fontWeight: 600, "& .MuiChip-icon": { color: "inherit" } }} />
      <Box sx={{ flexGrow: 1 }} />
      {st.action === "pause" ? (
        <Button variant="outlined" color="inherit" startIcon={<PauseRounded />} disabled={busy}
          onClick={() => onToggle(st)} sx={{ borderColor: "divider" }}>
          Pause all
        </Button>
      ) : (
        <Button variant="contained" startIcon={<PlayArrowRounded />} disabled={busy} onClick={() => onToggle(st)}>
          Start
        </Button>
      )}
      <Button variant="contained" startIcon={<PersonAddRounded />} component={RouterLink} to="/leads">
        Add leads
      </Button>
    </Stack>
  );
}

function EmptyLeads() {
  return (
    <Card sx={{ py: 7, px: 3, textAlign: "center" }}>
      <Box sx={{ width: 56, height: 56, mx: "auto", borderRadius: 3, display: "grid", placeItems: "center",
        bgcolor: "#1d2e2c", color: "primary.main" }}>
        <InboxRounded sx={{ fontSize: 30 }} />
      </Box>
      <Typography variant="h6" sx={{ mt: 2, fontSize: 17 }}>No leads yet</Typography>
      <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2.5, fontSize: 14 }}>
        Add leads and they start on the email schedule above.
      </Typography>
      <Button variant="contained" startIcon={<PersonAddRounded />} component={RouterLink} to="/leads">Add leads</Button>
    </Card>
  );
}

interface LeadsCardProps {
  board: Board;
  onLead: (l: BoardLead) => void;
  onCell: (l: BoardLead, emailNo: number) => void;
}

function LeadsCard({ board, onLead, onCell }: LeadsCardProps) {
  const [tab, setTab] = useState<TabKey>("all");
  const [query, setQuery] = useState("");
  const counts = useMemo(() => tabCounts(board.leads), [board.leads]);
  const shown = useMemo(() => board.leads.filter((l) => inTab(l, tab) && matchesQuery(l, query)),
    [board.leads, tab, query]);
  return (
    <Card>
      <Stack direction={{ xs: "column", md: "row" }} alignItems={{ md: "center" }} spacing={1}
        sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: "divider" }}>
        <Tabs value={tab} onChange={(_, v: TabKey) => setTab(v)} variant="scrollable" allowScrollButtonsMobile
          sx={{ minHeight: 40, flexGrow: 1, "& .MuiTabs-indicator": { display: "none" },
            "& .MuiTab-root": { minHeight: 34, py: 0.5, px: 1.5, mr: 0.5, borderRadius: 999, color: "text.secondary",
              fontWeight: 600, fontSize: 13.5 },
            "& .Mui-selected": { bgcolor: "#1d2e2c", color: "primary.main" } }}>
          {TABS.map((t) => (
            <Tab key={t.key} value={t.key} disableRipple label={
              <Box component="span">
                {t.label}
                <Box component="span" sx={{ ml: 0.75, opacity: 0.7 }}>{counts[t.key]}</Box>
              </Box>
            } />
          ))}
        </Tabs>
        <TextField size="small" placeholder="Name or company" value={query} onChange={(e) => setQuery(e.target.value)}
          sx={{ width: { xs: "100%", md: 240 }, "& .MuiOutlinedInput-root": { bgcolor: "background.default" } }}
          inputProps={{ "aria-label": "Search by name or company" }}
          InputProps={{ startAdornment: (
            <InputAdornment position="start"><SearchRounded fontSize="small" /></InputAdornment>
          ) }} />
      </Stack>
      <BoardTable schedule={board.schedule} tz={board.tz} leads={shown} onLead={onLead} onCell={onCell} />
    </Card>
  );
}

function PageSkeleton() {
  return (
    <Stack spacing={2.5}>
      <Skeleton variant="rounded" height={40} width={320} />
      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "repeat(2, 1fr)", lg: "repeat(4, 1fr)" } }}>
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={72} />)}
      </Box>
      <Skeleton variant="rounded" height={150} />
      <Skeleton variant="rounded" height={320} />
    </Stack>
  );
}

export default function CampaignPage() {
  const { name } = useUser();
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState<{ leadId: number; leadName: string; emailNo: number } | null>(null);
  const [leadId, setLeadId] = useState<number | null>(null);
  const [schedule, setSchedule] = useState<{ addRow: boolean } | null>(null);
  const [toggling, setToggling] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);

  const load = useCallback(async () => {
    try {
      setBoard(await getBoard());
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const busy = email !== null || leadId !== null || schedule !== null;
  useEffect(() => {
    if (busy) return undefined;
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [busy, load]);

  const done = useCallback((text: string) => {
    setToast({ severity: "success", text });
    void load();
  }, [load]);

  const toggle = async (st: HeaderState) => {
    setToggling(true);
    try {
      if (st.action === "pause") await pauseCampaign(name);
      else await startCampaign(name);
      done(st.action === "pause" ? "Campaign paused. Nothing more is sent until you start it." : "Campaign started.");
    } catch (e) {
      setToast({ severity: "error", text: errorText(e) });
    } finally {
      setToggling(false);
    }
  };

  const openEmail = (l: BoardLead, emailNo: number) => setEmail({ leadId: l.id, leadName: l.name, emailNo });
  const lead = board?.leads.find((l) => l.id === leadId) ?? null;
  // A failed refresh keeps the last board on screen with a warning; only a failed first load shows the error page.
  const state = pageState(loading, board ? null : error, board);

  return (
    <PageState state={state} onRetry={() => { setLoading(true); void load(); }} skeleton={<PageSkeleton />}>
      {(b) => (
        <Stack spacing={2.5} sx={{ maxWidth: 1400 }}>
          <Header board={b} busy={toggling} onToggle={toggle} />
          {error !== null && <Alert severity="warning">Could not refresh: {errorText(error)}</Alert>}
          <TotalsRow totals={b.totals} />
          <ScheduleStrip schedule={b.schedule} tz={b.tz} onEdit={(addRow) => setSchedule({ addRow })} />
          {b.leads.length === 0 ? <EmptyLeads /> : (
            <LeadsCard board={b} onLead={(l) => setLeadId(l.id)} onCell={openEmail} />
          )}
          <ScheduleDrawer open={schedule !== null} addRow={schedule?.addRow ?? false} board={b}
            onClose={() => setSchedule(null)} onSaved={done} />
          <EmailDrawer target={email} tz={b.tz} onClose={() => setEmail(null)} />
          <LeadDrawer lead={email ? null : lead} tz={b.tz} onClose={() => setLeadId(null)} onChanged={done}
            onEmail={openEmail} />
          <Snackbar open={toast !== null} autoHideDuration={4000} onClose={() => setToast(null)}
            anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
            <Alert severity={toast?.severity ?? "success"} variant="filled" onClose={() => setToast(null)}>
              {toast?.text}
            </Alert>
          </Snackbar>
        </Stack>
      )}
    </PageState>
  );
}
