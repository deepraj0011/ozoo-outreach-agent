import { useEffect, useState } from "react";
import { Alert, Box, Chip, CircularProgress, Drawer, IconButton, Stack, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { errorText, getJson } from "../api";
import { dateTime } from "../format";
import type { EmailDetail } from "../types";
import EmailPreview from "./EmailPreview";

const WHEN_LABEL: Record<EmailDetail["status"], string> = {
  sent: "Sent",
  handed: "With the sender since",
  planned: "Planned for",
  cancelled: "Was planned for",
  skipped: "Was planned for",
};

interface Props {
  target: { leadId: number; leadName: string; emailNo: number } | null;
  tz: string;
  onClose: () => void;
}

export default function EmailDrawer({ target, tz, onClose }: Props) {
  const [detail, setDetail] = useState<EmailDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    if (!target) return undefined;
    let live = true;
    getJson<EmailDetail>(`/demo/api/p0/leads/${target.leadId}/emails/${target.emailNo}`)
      .then((d) => live && setDetail(d))
      .catch((e) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [target]);

  return (
    <Drawer anchor="right" open={target !== null} onClose={onClose}
      PaperProps={{ sx: { width: { xs: "100%", sm: 640 }, p: 2.5 } }}>
      <Stack direction="row" alignItems="flex-start" justifyContent="space-between" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="overline" color="text.secondary">
            {target?.leadName}
          </Typography>
          <Typography variant="h6">
            Email {target?.emailNo}{detail?.template_name ? ` · ${detail.template_name}` : ""}
          </Typography>
        </Box>
        <IconButton onClick={onClose} aria-label="Close"><CloseIcon /></IconButton>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {!detail && !error && <Box sx={{ py: 6, textAlign: "center" }}><CircularProgress size={28} /></Box>}
      {detail && (
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
            <Typography sx={{ fontSize: 14 }}>
              <b>{WHEN_LABEL[detail.status]}</b> {dateTime(detail.sent_at ?? detail.when, tz)}
            </Typography>
            {detail.status === "cancelled" && <Chip size="small" label={`Cancelled${detail.reason ? `: ${detail.reason.replace(/_/g, " ")}` : ""}`} />}
            {detail.status === "skipped" && <Chip size="small" label="Skipped" />}
          </Stack>
          {(detail.status === "sent" || detail.status === "handed") && (
            <Stack direction="row" spacing={1}>
              <Chip size="small" color={detail.opens ? "success" : "default"} label={`${detail.opens} opens`} />
              <Chip size="small" color={detail.clicks ? "success" : "default"} label={`${detail.clicks} clicks`} />
              <Chip size="small" color={detail.visits ? "success" : "default"} label={`${detail.visits} page reads`} />
            </Stack>
          )}
          {detail.html_is_current_template && (
            <Alert severity="info" variant="outlined">
              Subject and plain text are exactly what was sent. The designed version is drawn from the template as
              it is now.
            </Alert>
          )}
          {detail.status === "planned" && (
            <Alert severity="info" variant="outlined">
              This is the template rendered for this lead now; it is filled in again when it is sent.
            </Alert>
          )}
          <EmailPreview subject={detail.subject} html={detail.html} text={detail.text} height="58vh" />
        </Stack>
      )}
    </Drawer>
  );
}
