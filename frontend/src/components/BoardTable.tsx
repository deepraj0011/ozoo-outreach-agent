import { Box, Button, Chip, Link, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Typography } from "@mui/material";
import ReplyIcon from "@mui/icons-material/ChatBubbleOutline";
import BlockIcon from "@mui/icons-material/DoNotDisturbOnOutlined";
import EventIcon from "@mui/icons-material/EventAvailableOutlined";
import BounceIcon from "@mui/icons-material/ErrorOutline";
import UnsubIcon from "@mui/icons-material/UnsubscribeOutlined";
import InboxIcon from "@mui/icons-material/InboxOutlined";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import type { Board, BoardLead, StopReason } from "../types";
import { shortDate } from "../format";
import EmailCell from "./EmailCell";
import { leadStatus } from "./status";

const STOP_BANNER: Record<StopReason, { icon: ReactNode; text: string; bg: string; fg: string }> = {
  replied: { icon: <ReplyIcon />, text: "Replied", bg: "rgba(15,118,110,0.08)", fg: "#0f766e" },
  booked: { icon: <EventIcon />, text: "Demo booked", bg: "rgba(21,128,61,0.08)", fg: "#15803d" },
  unsubscribed: { icon: <UnsubIcon />, text: "Unsubscribed", bg: "rgba(185,28,28,0.07)", fg: "#b91c1c" },
  bounced: { icon: <BounceIcon />, text: "Email bounced", bg: "rgba(185,28,28,0.07)", fg: "#b91c1c" },
  stopped: { icon: <BlockIcon />, text: "Stopped by hand", bg: "rgba(107,114,128,0.08)", fg: "#4b5563" },
};

function StopSpan({ lead, span, tz }: { lead: BoardLead; span: number; tz: string }) {
  const b = STOP_BANNER[lead.stop_reason as StopReason];
  const when = shortDate(lead.stopped_at, tz);
  return (
    <TableCell colSpan={span} sx={{ py: 1 }}>
      <Stack direction="row" alignItems="center" spacing={1}
        sx={{ bgcolor: b.bg, color: b.fg, borderRadius: 1.5, px: 1.5, py: 1, "& svg": { fontSize: 18 } }}>
        {b.icon}
        <Typography sx={{ fontSize: 13, fontWeight: 600 }}>
          {b.text}{when ? ` ${when}` : ""} — remaining emails stopped
        </Typography>
      </Stack>
    </TableCell>
  );
}

interface Props {
  board: Board;
  leads: BoardLead[];
  onCell: (lead: BoardLead, emailNo: number) => void;
  onLead: (lead: BoardLead) => void;
}

function LeadCell({ lead, onLead }: { lead: BoardLead; onLead: (l: BoardLead) => void }) {
  const st = leadStatus(lead);
  const sub = [lead.company, lead.role].filter(Boolean).join(" · ");
  return (
    <TableCell sx={{ position: "sticky", left: 0, bgcolor: "background.paper", zIndex: 1, minWidth: 220, maxWidth: 280,
      borderRight: 1, borderRightColor: "divider" }}>
      <Link component="button" underline="hover" onClick={() => onLead(lead)}
        sx={{ fontWeight: 700, color: "text.primary", fontSize: 14, textAlign: "left" }}>
        {lead.name}
      </Link>
      <Typography noWrap sx={{ fontSize: 12, color: "text.secondary" }} title={sub}>{sub}</Typography>
      <Chip size="small" label={st.label} color={st.color} variant={st.color === "default" ? "outlined" : "filled"}
        sx={{ mt: 0.5, height: 20, fontSize: 11 }} />
    </TableCell>
  );
}

export default function BoardTable({ board, leads, onCell, onLead }: Props) {
  const navigate = useNavigate();
  const cols = board.schedule.map((s) => s.email_no);
  if (board.leads.length === 0) {
    return (
      <Paper variant="outlined" sx={{ p: 6, textAlign: "center" }}>
        <InboxIcon sx={{ fontSize: 48, color: "text.disabled" }} />
        <Typography variant="h6" sx={{ mt: 1 }}>No leads yet</Typography>
        <Typography color="text.secondary" sx={{ mb: 2 }}>Add leads and they start on the schedule above.</Typography>
        <Button variant="contained" onClick={() => navigate("/leads")}>Add leads</Button>
      </Paper>
    );
  }
  return (
    <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: "calc(100vh - 220px)", minHeight: 240 }}>
      <Table stickyHeader size="small">
        <TableHead>
          <TableRow>
            <TableCell sx={{ position: "sticky", left: 0, zIndex: 3, minWidth: 220 }}>Lead</TableCell>
            {board.schedule.map((s) => (
              <TableCell key={s.email_no} sx={{ minWidth: 150 }}>
                Email {s.email_no}
                <Typography noWrap sx={{ fontSize: 11, color: "text.secondary", fontWeight: 400, maxWidth: 170 }}
                  title={s.template_name}>
                  Day {s.day} · {s.template_name}
                </Typography>
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {leads.map((lead) => {
            const byNo = new Map(lead.emails.map((e) => [e.email_no, e]));
            const firstOpen = cols.findIndex((n) => byNo.get(n)?.status !== "sent");
            const merge = lead.stop_reason !== null && firstOpen >= 0;
            const shown = merge ? cols.slice(0, firstOpen) : cols;
            return (
              <TableRow key={lead.id} hover>
                <LeadCell lead={lead} onLead={onLead} />
                {shown.map((n) => (
                  <TableCell key={n} sx={{ py: 0.75, px: 0.75 }}>
                    <EmailCell cell={byNo.get(n)} tz={board.tz} onClick={() => onCell(lead, n)} />
                  </TableCell>
                ))}
                {merge && <StopSpan lead={lead} span={cols.length - firstOpen} tz={board.tz} />}
              </TableRow>
            );
          })}
          {leads.length === 0 && (
            <TableRow>
              <TableCell colSpan={cols.length + 1}>
                <Box sx={{ py: 4, textAlign: "center", color: "text.secondary" }}>No leads match this filter.</Box>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
