import { Box, Button, Card, Stack, Typography } from "@mui/material";
import { actionLabel } from "../lib/connectors";
import type { Connector } from "../types";
import { ConnectorTile, StatusChip } from "./connectorLook";

interface Props {
  connector: Connector;
  onOpen: (c: Connector) => void;
}

export default function ConnectorCard({ connector: c, onOpen }: Props) {
  const soon = c.status === "coming_soon";
  const primary = c.status === "available";
  return (
    <Card sx={{ p: 2, display: "flex", flexDirection: "column", gap: 1.25, height: "100%" }}>
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <ConnectorTile id={c.id} name={c.name} />
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography noWrap sx={{ fontWeight: 700 }}>{c.name}</Typography>
          <Typography noWrap variant="caption" color="text.secondary">{c.account}</Typography>
        </Box>
        <StatusChip status={c.status} />
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>{c.detail}</Typography>
      <Stack direction="row" alignItems="center" spacing={1}
        sx={{ borderTop: 1, borderColor: "divider", pt: 1.25 }}>
        <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1 }}>{c.basis}</Typography>
        <Button size="small" variant={primary ? "contained" : "outlined"} color={primary ? "primary" : "inherit"}
          disabled={soon} onClick={() => onOpen(c)} sx={primary ? undefined : { borderColor: "divider" }}>
          {actionLabel(c.status)}
        </Button>
      </Stack>
    </Card>
  );
}
