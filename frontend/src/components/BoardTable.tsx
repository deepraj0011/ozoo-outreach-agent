import { Avatar, Box, ButtonBase, Chip, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip,
  Typography } from "@mui/material";
import type { BoardLead, Cell, ScheduleRow } from "../types";
import { clip, dotHint, dotLook, initials, nextLabel, sentCount, trackLabel, type DotLook } from "../lib/campaign";
import { Marker } from "./ScheduleRuler";
import { leadStatus, TONE_SX } from "./status";

/** D · Board with dot tracks: one row per lead, one small dot per scheduled email (fits 15 emails at 1280px). */

const DOT = 16;
const LEAD_W = 210;
const STATUS_W = 128;
const NEXT_W = 210;
const TEAL = "#2dd4bf";
const GREEN = "#4ade80";
const AMBER = "#fbbf24";
const GREY = "#5b626c";

const DOT_SX: Record<DotLook, object> = {
  sent: { bgcolor: GREEN, border: `2px solid ${GREEN}` },
  active: { bgcolor: TEAL, border: `2px solid ${TEAL}`, boxShadow: `0 0 0 3px rgba(45,212,191,0.28)` },
  planned: { bgcolor: "transparent", border: `2px solid ${GREY}` },
  late: { bgcolor: "transparent", border: `2px solid ${AMBER}`, boxShadow: `0 0 0 3px rgba(251,191,36,0.22)` },
  stopped: { bgcolor: GREY, border: 0, height: 3, borderRadius: 2, width: 10 },
};

interface Props {
  schedule: ScheduleRow[];
  tz: string;
  leads: BoardLead[];
  onCell: (lead: BoardLead, emailNo: number) => void;
  onLead: (lead: BoardLead) => void;
}

function Clipped({ text, sx }: { text: string; sx: object }) {
  const c = clip(text);
  const node = <Typography noWrap sx={sx}>{c.text}</Typography>;
  return c.clipped ? <Tooltip title={text}>{node}</Tooltip> : node;
}

function LeadCell({ lead, onLead }: { lead: BoardLead; onLead: (l: BoardLead) => void }) {
  return (
    <TableCell>
      <ButtonBase onClick={(e) => { e.stopPropagation(); onLead(lead); }} aria-label={`Open ${lead.name}`}
        sx={{ display: "flex", alignItems: "center", gap: 1.25, justifyContent: "flex-start", textAlign: "left",
          borderRadius: 1.5, p: 0.5, ml: -0.5, maxWidth: "100%", "&:hover": { bgcolor: "rgba(255,255,255,0.04)" } }}>
        <Avatar sx={{ width: 32, height: 32, fontSize: 12, fontWeight: 700, bgcolor: "#1d2e2c", color: "primary.main" }}>
          {initials(lead.name)}
        </Avatar>
        <Box sx={{ minWidth: 0 }}>
          <Clipped text={lead.name} sx={{ fontWeight: 600, fontSize: 13.5 }} />
          {lead.company && <Clipped text={lead.company} sx={{ fontSize: 12, color: "text.secondary" }} />}
        </Box>
      </ButtonBase>
    </TableCell>
  );
}

function Dot({ cell, tz, onClick }: { cell: Cell; tz: string; onClick: () => void }) {
  const look = dotLook(cell);
  const hint = dotHint(cell, tz);
  return (
    <Tooltip title={hint} placement="top" enterDelay={250}>
      <ButtonBase aria-label={hint} onClick={(e) => { e.stopPropagation(); onClick(); }}
        sx={{ width: DOT + 8, height: DOT + 8, borderRadius: "50%", display: "grid", placeItems: "center", mx: "auto",
          "&:hover, &.Mui-focusVisible": { bgcolor: "rgba(255,255,255,0.08)" } }}>
        <Box sx={{ width: DOT, height: DOT, borderRadius: "50%", boxSizing: "border-box", ...DOT_SX[look] }} />
      </ButtonBase>
    </Tooltip>
  );
}

const trackGrid = (n: number) => ({ display: "grid", gridTemplateColumns: `repeat(${Math.max(n, 1)}, minmax(0, 1fr))`,
  alignItems: "center", justifyItems: "center" });

function TrackHeader({ cols }: { cols: ScheduleRow[] }) {
  return (
    <Box sx={trackGrid(cols.length)}>
      {cols.map((r) => (
        <Box key={r.email_no} sx={{ display: "grid", justifyItems: "center", gap: 0.25 }}>
          <Marker kind={r.mode} n={r.email_no} size={18} />
          <Typography sx={{ fontSize: 10.5, color: "text.secondary", lineHeight: 1.2 }} noWrap>{trackLabel(r)}</Typography>
        </Box>
      ))}
    </Box>
  );
}

function Track({ lead, cols, tz, onCell }: { lead: BoardLead; cols: ScheduleRow[]; tz: string;
  onCell: (lead: BoardLead, emailNo: number) => void }) {
  const byNo = new Map(lead.emails.map((e) => [e.email_no, e]));
  return (
    <Box sx={{ ...trackGrid(cols.length), position: "relative",
      "&::before": { content: '""', position: "absolute", left: 8, right: 8, top: "50%", height: "1px", bgcolor: "divider" } }}>
      {cols.map((r) => {
        const cell = byNo.get(r.email_no);
        return cell ? <Box key={r.email_no} sx={{ zIndex: 1 }}><Dot cell={cell} tz={tz} onClick={() => onCell(lead, r.email_no)} /></Box>
          : <Box key={r.email_no} aria-label={`Email ${r.email_no}: not planned for this lead`} />;
      })}
    </Box>
  );
}

export default function BoardTable({ schedule, tz, leads, onCell, onLead }: Props) {
  const cols = [...schedule].sort((a, b) => a.email_no - b.email_no);
  return (
    <TableContainer sx={{ maxHeight: "calc(100vh - 260px)", minHeight: 200 }}>
      <Table stickyHeader size="small"
        sx={{ tableLayout: "fixed", "& td, & th": { borderColor: "divider", px: 1.25 },
          "& th": { bgcolor: "background.paper", py: 1, fontSize: 12.5 }, "& td": { py: 0.75 },
          "& td:first-of-type, & th:first-of-type": { pl: 2 } }}>
        <colgroup>
          <col style={{ width: LEAD_W }} />
          <col />
          <col style={{ width: STATUS_W }} />
          <col style={{ width: NEXT_W }} />
        </colgroup>
        <TableHead>
          <TableRow>
            <TableCell>Lead</TableCell>
            <TableCell aria-label="Emails"><TrackHeader cols={cols} /></TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Next</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {leads.map((lead) => {
            const st = leadStatus(lead);
            const count = sentCount(lead);
            const next = nextLabel(lead, tz);
            return (
              <TableRow key={lead.id} hover onClick={() => onLead(lead)} sx={{ cursor: "pointer" }}>
                <LeadCell lead={lead} onLead={onLead} />
                <TableCell><Track lead={lead} cols={cols} tz={tz} onCell={onCell} /></TableCell>
                <TableCell>
                  <Chip size="small" label={st.label} sx={{ ...TONE_SX[st.tone], fontWeight: 600, fontSize: 12, maxWidth: "100%" }} />
                  <Typography sx={{ fontSize: 11.5, color: "text.secondary", mt: 0.25 }} noWrap>
                    {count.sent} of {count.total} sent
                  </Typography>
                </TableCell>
                <TableCell>
                  <Typography title={next} sx={{ fontSize: 13, fontWeight: 500, lineHeight: 1.35, overflow: "hidden",
                    display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                    color: lead.stop_reason || lead.paused ? "text.secondary" : "text.primary" }}>{next}</Typography>
                </TableCell>
              </TableRow>
            );
          })}
          {leads.length === 0 && (
            <TableRow>
              <TableCell colSpan={4} sx={{ py: 6, textAlign: "center", color: "text.secondary" }}>
                No leads match this view.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
