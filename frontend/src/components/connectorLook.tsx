import type { ReactNode } from "react";
import { Box, Chip } from "@mui/material";
import { alpha, type Theme } from "@mui/material/styles";
import DnsRounded from "@mui/icons-material/DnsRounded";
import RocketLaunchRounded from "@mui/icons-material/RocketLaunchRounded";
import CalendarMonthRounded from "@mui/icons-material/CalendarMonthRounded";
import GroupsRounded from "@mui/icons-material/GroupsRounded";
import VerifiedRounded from "@mui/icons-material/VerifiedRounded";
import ExtensionRounded from "@mui/icons-material/ExtensionRounded";
import gmail from "../assets/logos/gmail.svg";
import googlecalendar from "../assets/logos/googlecalendar.svg";
import caldotcom from "../assets/logos/caldotcom.svg";
import whatsapp from "../assets/logos/whatsapp.svg";
import anthropic from "../assets/logos/anthropic.svg";
import { statusLook } from "../lib/connectors";
import type { ConnectorStatus } from "../types";

const LOGOS: Record<string, string> = { gmail, google_calendar: googlecalendar, cal: caldotcom, whatsapp, llm: anthropic };

const ICONS: Record<string, ReactNode> = {
  mailboxes: <DnsRounded />,
  instantly: <RocketLaunchRounded />,
  outlook_calendar: <CalendarMonthRounded />,
  teams: <GroupsRounded />,
  verify: <VerifiedRounded />,
};

/** Brand marks sit on a light tile so dark marks stay visible; tools without a mark get a tinted icon tile. */
export function ConnectorTile({ id, name, size = 38 }: { id: string; name: string; size?: number }) {
  const logo = LOGOS[id];
  const inner = Math.round(size * 0.58);
  return (
    <Box sx={(t: Theme) => ({
      width: size, height: size, borderRadius: 2.5, display: "grid", placeItems: "center", flex: "none",
      bgcolor: logo ? t.palette.grey[100] : alpha(t.palette.primary.main, 0.14),
      color: "primary.main",
    })}>
      {logo
        ? <img src={logo} alt={name} width={inner} height={inner} />
        : <Box sx={{ display: "grid", "& svg": { fontSize: inner + 2 } }}>{ICONS[id] ?? <ExtensionRounded />}</Box>}
    </Box>
  );
}

export function StatusChip({ status }: { status: ConnectorStatus }) {
  const { label, tone } = statusLook(status);
  return (
    <Chip size="small" label={label} sx={(t: Theme) => {
      const c = tone === "muted" ? t.palette.text.secondary : t.palette[tone].main;
      return {
        bgcolor: tone === "muted" ? t.palette.background.default : alpha(c, 0.16),
        color: c, fontWeight: 600, height: 22, fontSize: 11.5,
      };
    }} />
  );
}
