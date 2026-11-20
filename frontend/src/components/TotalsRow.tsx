import type { ReactNode } from "react";
import { Box, Card, Typography } from "@mui/material";
import GroupRounded from "@mui/icons-material/GroupRounded";
import SendRounded from "@mui/icons-material/SendRounded";
import MarkEmailReadRounded from "@mui/icons-material/MarkEmailReadRounded";
import BlockRounded from "@mui/icons-material/BlockRounded";
import type { Totals } from "../types";

interface Tile {
  label: string;
  icon: ReactNode;
  value: number;
  of?: number;
}

function tiles(t: Totals): Tile[] {
  return [
    { label: "Leads in sequence", icon: <GroupRounded />, value: Math.max(0, t.leads - t.stopped) },
    { label: "Sent today", icon: <SendRounded />, value: t.sent_today, of: t.sent_today + t.today },
    { label: "Replies", icon: <MarkEmailReadRounded />, value: t.replied },
    { label: "Stopped or unsubscribed", icon: <BlockRounded />, value: t.stopped },
  ];
}

export default function TotalsRow({ totals }: { totals: Totals }) {
  return (
    <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "repeat(2, 1fr)", lg: "repeat(4, 1fr)" } }}>
      {tiles(totals).map((s) => (
        <Card key={s.label} sx={{ px: 2, py: 1.75, display: "flex", alignItems: "center", gap: 1.5 }}>
          <Box sx={{ width: 40, height: 40, borderRadius: 2.25, display: "grid", placeItems: "center", flexShrink: 0,
            color: "primary.main", bgcolor: "#1d2e2c", "& svg": { fontSize: 22 } }}>
            {s.icon}
          </Box>
          <Box sx={{ minWidth: 0 }}>
            <Typography component="div" sx={{ fontSize: 22, fontWeight: 700, lineHeight: 1.15 }}>
              {s.value}
              {s.of !== undefined && (
                <Box component="span" sx={{ fontSize: 13, fontWeight: 500, color: "text.secondary", ml: 0.75 }}>
                  of {s.of}
                </Box>
              )}
            </Typography>
            <Typography noWrap variant="body2" color="text.secondary" sx={{ fontSize: 12.5 }}>{s.label}</Typography>
          </Box>
        </Card>
      ))}
    </Box>
  );
}
