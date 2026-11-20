import { useState } from "react";
import { Box, Drawer, IconButton, Snackbar, Stack, Step, StepContent, StepLabel, Stepper, Tooltip, Typography } from "@mui/material";
import CloseRounded from "@mui/icons-material/CloseRounded";
import ContentCopyRounded from "@mui/icons-material/ContentCopyRounded";
import type { Connector } from "../types";
import { ConnectorTile, StatusChip } from "./connectorLook";

const WIDTH = 380;

interface Props {
  connector: Connector | null;
  onClose: () => void;
}

export default function ConnectorDrawer({ connector: c, onClose }: Props) {
  const [notice, setNotice] = useState<string | null>(null);

  // Only the variable NAME is ever copied; the API carries no values.
  const copy = async (key: string) => {
    try {
      await navigator.clipboard.writeText(key);
      setNotice(`Copied ${key}`);
    } catch {
      setNotice("Could not copy. Select the name and copy it by hand.");
    }
  };

  return (
    <Drawer anchor="right" open={c !== null} onClose={onClose}
      PaperProps={{ sx: { width: { xs: "100%", sm: WIDTH }, p: 2.5 } }}>
      {c && (
        <Stack spacing={2.5}>
          <Stack direction="row" alignItems="center" spacing={1.25}>
            <ConnectorTile id={c.id} name={c.name} account={c.account} size={34} />
            <Box sx={{ flexGrow: 1, minWidth: 0 }}>
              <Typography variant="h6" noWrap sx={{ fontSize: 16 }}>{c.name}</Typography>
              <Box sx={{ mt: 0.25 }}><StatusChip status={c.status} /></Box>
            </Box>
            <IconButton onClick={onClose} aria-label="Close"><CloseRounded /></IconButton>
          </Stack>
          <Typography variant="body2" color="text.secondary">{c.detail}</Typography>

          {c.setup.steps.length > 0 && (
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Setup</Typography>
              <Stepper orientation="vertical" nonLinear activeStep={-1}>
                {c.setup.steps.map((s, i) => (
                  <Step key={i} active expanded>
                    <StepLabel><Typography variant="body2" sx={{ fontWeight: 600 }}>{s}</Typography></StepLabel>
                    <StepContent />
                  </Step>
                ))}
              </Stepper>
            </Box>
          )}

          {c.setup.env_keys.length > 0 && (
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Settings in the .env file</Typography>
              <Stack spacing={0.75}>
                {c.setup.env_keys.map((k) => (
                  <Stack key={k} direction="row" alignItems="center"
                    sx={{ border: 1, borderColor: "divider", borderRadius: 2, pl: 1.5, pr: 0.5, bgcolor: "background.default" }}>
                    <Typography sx={{ flexGrow: 1, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12.5, py: 0.75 }}>
                      {k}
                    </Typography>
                    <Tooltip title="Copy name">
                      <IconButton size="small" aria-label={`Copy ${k}`} onClick={() => void copy(k)}>
                        <ContentCopyRounded fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                ))}
              </Stack>
            </Box>
          )}

          <Typography variant="caption" color="text.secondary">Restart the agent to apply changes.</Typography>
        </Stack>
      )}
      <Snackbar open={notice !== null} autoHideDuration={2500} onClose={() => setNotice(null)}
        message={notice ?? ""} anchorOrigin={{ vertical: "bottom", horizontal: "center" }} />
    </Drawer>
  );
}
