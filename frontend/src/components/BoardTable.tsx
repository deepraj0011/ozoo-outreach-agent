import { Avatar, Box, Chip, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip,
  Typography } from "@mui/material";
import type { BoardLead, ScheduleRow } from "../types";
import { clip, initials } from "../lib/campaign";
import EmailCell from "./EmailCell";
import { leadStatus, TONE_SX } from "./status";

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

function LeadCell({ lead }: { lead: BoardLead }) {
  return (
    <TableCell sx={{ minWidth: 240 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.25 }}>
        <Avatar sx={{ width: 34, height: 34, fontSize: 12.5, fontWeight: 700, bgcolor: "#1d2e2c", color: "primary.main" }}>
          {initials(lead.name)}
        </Avatar>
        <Box sx={{ minWidth: 0 }}>
          <Clipped text={lead.name} sx={{ fontWeight: 600, fontSize: 13.5 }} />
          {lead.company && <Clipped text={lead.company} sx={{ fontSize: 12, color: "text.secondary" }} />}
        </Box>
      </Box>
    </TableCell>
  );
}

export default function BoardTable({ schedule, tz, leads, onCell, onLead }: Props) {
  const cols = [...schedule].sort((a, b) => a.email_no - b.email_no).map((s) => s.email_no);
  return (
    <TableContainer sx={{ maxHeight: "calc(100vh - 260px)", minHeight: 200 }}>
      <Table stickyHeader size="small"
        sx={{ "& td, & th": { borderColor: "divider" }, "& th": { bgcolor: "background.paper", py: 1.25, fontSize: 12.5 },
          "& td": { py: 1.25 } }}>
        <TableHead>
          <TableRow>
            <TableCell sx={{ pl: 2 }}>Lead</TableCell>
            <TableCell>Status</TableCell>
            {cols.map((n) => <TableCell key={n} sx={{ minWidth: 120 }}>Email {n}</TableCell>)}
          </TableRow>
        </TableHead>
        <TableBody>
          {leads.map((lead) => {
            const byNo = new Map(lead.emails.map((e) => [e.email_no, e]));
            const st = leadStatus(lead);
            return (
              <TableRow key={lead.id} hover onClick={() => onLead(lead)} sx={{ cursor: "pointer",
                "& td:first-of-type": { pl: 2 } }}>
                <LeadCell lead={lead} />
                <TableCell>
                  <Chip size="small" label={st.label} sx={{ ...TONE_SX[st.tone], fontWeight: 600, fontSize: 12 }} />
                </TableCell>
                {cols.map((n) => (
                  <TableCell key={n}>
                    <EmailCell cell={byNo.get(n)} tz={tz} onClick={() => onCell(lead, n)} />
                  </TableCell>
                ))}
              </TableRow>
            );
          })}
          {leads.length === 0 && (
            <TableRow>
              <TableCell colSpan={cols.length + 2} sx={{ py: 6, textAlign: "center", color: "text.secondary" }}>
                No leads match this view.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
