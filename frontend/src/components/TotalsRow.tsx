import { Box, Card, Typography } from "@mui/material";
import PeopleIcon from "@mui/icons-material/PeopleAltOutlined";
import SendIcon from "@mui/icons-material/SendOutlined";
import TodayIcon from "@mui/icons-material/TodayOutlined";
import ReplyIcon from "@mui/icons-material/ChatBubbleOutline";
import StopIcon from "@mui/icons-material/DoNotDisturbOnOutlined";
import UnsubIcon from "@mui/icons-material/UnsubscribeOutlined";
import type { ReactNode } from "react";
import type { Totals } from "../types";

interface Stat {
  key: keyof Totals;
  label: string;
  icon: ReactNode;
  color: string;
}

const STATS: Stat[] = [
  { key: "leads", label: "Leads", icon: <PeopleIcon />, color: "#3730a3" },
  { key: "sent", label: "Emails sent", icon: <SendIcon />, color: "#15803d" },
  { key: "today", label: "Going today", icon: <TodayIcon />, color: "#1d4ed8" },
  { key: "replied", label: "Replied", icon: <ReplyIcon />, color: "#0f766e" },
  { key: "stopped", label: "Stopped", icon: <StopIcon />, color: "#6b7280" },
  { key: "unsubscribed", label: "Unsubscribed", icon: <UnsubIcon />, color: "#b91c1c" },
];

export default function TotalsRow({ totals }: { totals: Totals }) {
  return (
    <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(3, 1fr)", lg: "repeat(6, 1fr)" } }}>
      {STATS.map((s) => (
        <Card key={s.key} sx={{ p: 1.75, display: "flex", alignItems: "center", gap: 1.5 }}>
          <Box sx={{ width: 38, height: 38, borderRadius: 2, display: "grid", placeItems: "center",
            color: s.color, bgcolor: `${s.color}14` }}>
            {s.icon}
          </Box>
          <Box>
            <Typography sx={{ fontSize: 22, fontWeight: 700, lineHeight: 1.1 }}>{totals[s.key]}</Typography>
            <Typography variant="caption" color="text.secondary">{s.label}</Typography>
          </Box>
        </Card>
      ))}
    </Box>
  );
}
