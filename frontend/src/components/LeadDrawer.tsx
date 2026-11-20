import { useEffect, useState } from "react";
import { Alert, Box, Button, Chip, Divider, Drawer, IconButton, List, ListItemButton, ListItemText, Stack,
  Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import PauseIcon from "@mui/icons-material/PauseCircleOutline";
import PlayIcon from "@mui/icons-material/PlayCircleOutline";
import SendIcon from "@mui/icons-material/SendOutlined";
import StopIcon from "@mui/icons-material/DoNotDisturbOnOutlined";
import { errorText, postJson } from "../api";
import { dateTime, shortDate } from "../format";
import type { BoardLead, Cell } from "../types";
import { useUser } from "../user";
import ConfirmDialog from "./ConfirmDialog";
import { canControl, leadStatus } from "./status";

type Action = "pause" | "resume" | "send_now" | "stop";

const DONE: Record<Action, string> = {
  pause: "Emails paused for this lead.",
  resume: "Emails resumed. Anything overdue goes in the next send slot.",
  send_now: "The next email was handed to the sender.",
  stop: "Emails stopped for this lead.",
};

function cellLine(c: Cell, tz: string): string {
  switch (c.status) {
    case "sent": return `Sent ${dateTime(c.sent_at ?? c.date, tz)} · ${c.opens} opens · ${c.clicks} clicks`;
    case "today": return `Going today, ${dateTime(c.date, tz)}`;
    case "handed": return `With the sender, ${shortDate(c.date, tz)}`;
    case "planned": return `Planned ${dateTime(c.date, tz)}${c.late ? " (late)" : ""}`;
    case "skipped": return "Skipped";
    default: return `Cancelled${c.reason ? ` (${c.reason.replace(/_/g, " ")})` : ""}`;
  }
}

interface Props {
  lead: BoardLead | null;
  tz: string;
  onClose: () => void;
  onChanged: (message: string) => void;
  onEmail: (lead: BoardLead, emailNo: number) => void;
}

export default function LeadDrawer({ lead, tz, onClose, onChanged, onEmail }: Props) {
  const { name } = useUser();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);

  useEffect(() => setError(null), [lead?.id]);

  const run = async (action: Action) => {
    if (!lead) return;
    setBusy(true);
    setError(null);
    try {
      await postJson(`/demo/api/p0/leads/${lead.id}/${action}`, { by: name });
      onChanged(DONE[action]);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const st = lead ? leadStatus(lead) : null;
  const control = lead ? canControl(lead) : false;
  return (
    <Drawer anchor="right" open={lead !== null} onClose={onClose}
      PaperProps={{ sx: { width: { xs: "100%", sm: 460 }, p: 2.5 } }}>
      {lead && st && (
        <>
          <Stack direction="row" alignItems="flex-start" justifyContent="space-between">
            <Box>
              <Typography variant="h6">{lead.name}</Typography>
              <Typography color="text.secondary" sx={{ fontSize: 14 }}>
                {[lead.role, lead.company].filter(Boolean).join(" at ")}
              </Typography>
              <Chip size="small" label={st.label} color={st.color} sx={{ mt: 1 }} />
            </Box>
            <IconButton onClick={onClose} aria-label="Close"><CloseIcon /></IconButton>
          </Stack>
          <Divider sx={{ my: 2 }} />
          <Typography variant="subtitle2" sx={{ mb: 1 }}>Actions</Typography>
          {!control && (
            <Alert severity="info" variant="outlined" sx={{ mb: 1.5 }}>
              This lead's scheduled emails have ended, so there is nothing to pause or send.
            </Alert>
          )}
          {error && <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError(null)}>{error}</Alert>}
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {lead.paused ? (
              <Button variant="outlined" startIcon={<PlayIcon />} disabled={!control || busy} onClick={() => run("resume")}>
                Resume
              </Button>
            ) : (
              <Button variant="outlined" startIcon={<PauseIcon />} disabled={!control || busy} onClick={() => run("pause")}>
                Pause
              </Button>
            )}
            <Button variant="contained" startIcon={<SendIcon />} disabled={!control || busy} onClick={() => run("send_now")}>
              Send next now
            </Button>
            <Button color="error" variant="outlined" startIcon={<StopIcon />} disabled={!control || busy}
              onClick={() => setConfirmStop(true)}>
              Stop emails
            </Button>
          </Stack>
          <Divider sx={{ my: 2 }} />
          <Typography variant="subtitle2">Emails</Typography>
          <List dense disablePadding>
            {lead.emails.map((c) => (
              <ListItemButton key={c.email_no} onClick={() => onEmail(lead, c.email_no)} sx={{ borderRadius: 1.5 }}>
                <ListItemText primary={`Email ${c.email_no}`} secondary={cellLine(c, tz)}
                  primaryTypographyProps={{ fontWeight: 600, fontSize: 14 }} />
              </ListItemButton>
            ))}
          </List>
          <ConfirmDialog
            open={confirmStop}
            title="Stop emails to this lead?"
            text={`No more scheduled emails will go to ${lead.name}. This cannot be undone from here.`}
            confirm="Stop emails"
            onClose={() => setConfirmStop(false)}
            onConfirm={() => {
              setConfirmStop(false);
              void run("stop");
            }}
          />
        </>
      )}
    </Drawer>
  );
}
