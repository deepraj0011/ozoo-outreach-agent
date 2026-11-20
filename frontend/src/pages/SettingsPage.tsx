import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Alert, Box, Card, Chip, List, ListItemButton, ListItemIcon, ListItemText, Skeleton, Stack, Switch,
  Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import ScheduleSendRounded from "@mui/icons-material/ScheduleSendRounded";
import ShieldRounded from "@mui/icons-material/ShieldRounded";
import NotificationsRounded from "@mui/icons-material/NotificationsRounded";
import BusinessRounded from "@mui/icons-material/BusinessRounded";
import GroupRounded from "@mui/icons-material/GroupRounded";
import TuneRounded from "@mui/icons-material/TuneRounded";
import { getSettings } from "../api";
import { pageState } from "../lib/pageState";
import type { SettingRow, SettingsSection, SettingsView } from "../types";
import PageState from "../components/PageState";

const SECTION_ICONS: Record<string, ReactNode> = {
  sending: <ScheduleSendRounded />,
  safety: <ShieldRounded />,
  alerts: <NotificationsRounded />,
  company: <BusinessRounded />,
  team: <GroupRounded />,
};

function Value({ value, label }: { value: SettingRow["value"]; label: string }) {
  if (Array.isArray(value)) {
    return (
      <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap" justifyContent={{ sm: "flex-end" }}>
        {value.map((v) => (
          <Chip key={v} size="small" label={v}
            sx={(t) => ({ bgcolor: alpha(t.palette.primary.main, 0.14), color: "primary.main", fontWeight: 600 })} />
        ))}
      </Stack>
    );
  }
  if (typeof value === "boolean") {
    return <Switch checked={value} disabled inputProps={{ "aria-label": label }} />;
  }
  if (value === "") return <Typography color="text.secondary" sx={{ fontSize: 14 }}>Not set</Typography>;
  return <Typography sx={{ fontWeight: 600, fontSize: 14 }}>{String(value)}</Typography>;
}

function Section({ section }: { section: SettingsSection }) {
  return (
    <Card id={`section-${section.id}`} sx={{ scrollMarginTop: 88 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 700, px: 2.25, py: 1.75, borderBottom: 1, borderColor: "divider" }}>
        {section.title}
      </Typography>
      {section.rows.map((r) => (
        <Box key={r.label} sx={{ display: "grid", gap: 1, alignItems: "center", px: 2.25, py: 1.75,
          gridTemplateColumns: { xs: "1fr", sm: "1fr minmax(0, 280px)" },
          "&:not(:last-of-type)": { borderBottom: 1, borderColor: "divider" } }}>
          <Box>
            <Typography sx={{ fontWeight: 600, fontSize: 14 }}>{r.label}</Typography>
            <Typography variant="caption" color="text.secondary">{r.help}</Typography>
          </Box>
          <Box sx={{ display: "flex", justifyContent: { sm: "flex-end" }, textAlign: { sm: "right" } }}>
            <Value value={r.value} label={r.label} />
          </Box>
        </Box>
      ))}
    </Card>
  );
}

function SettingsBody({ view }: { view: SettingsView }) {
  const [active, setActive] = useState(view.sections[0]?.id ?? "");
  const pick = (id: string) => {
    setActive(id);
    document.getElementById(`section-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <Box sx={{ display: "grid", gap: 3, gridTemplateColumns: { xs: "1fr", md: "190px minmax(0, 1fr)" }, alignItems: "start" }}>
      <List dense sx={{ position: { md: "sticky" }, top: { md: 88 }, display: "flex", flexDirection: { xs: "row", md: "column" },
        gap: 0.25, overflowX: "auto" }}>
        {view.sections.map((s) => (
          <ListItemButton key={s.id} selected={active === s.id} onClick={() => pick(s.id)}
            sx={{ borderRadius: 2, flex: "none", color: "text.secondary", "&.Mui-selected": { bgcolor: "background.paper", color: "text.primary" } }}>
            <ListItemIcon sx={{ minWidth: 34, color: "inherit" }}>{SECTION_ICONS[s.id] ?? <TuneRounded />}</ListItemIcon>
            <ListItemText primary={s.title} primaryTypographyProps={{ fontWeight: 600, fontSize: 14 }} />
          </ListItemButton>
        ))}
      </List>
      <Stack spacing={2}>
        <Alert severity="info">These come from the agent's .env file. Change them there, then restart the agent.</Alert>
        {view.sections.map((s) => <Section key={s.id} section={s} />)}
      </Stack>
    </Box>
  );
}

export default function SettingsPage() {
  const [data, setData] = useState<SettingsView | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setData(await getSettings());
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h5">Settings</Typography>
        <Typography color="text.secondary" sx={{ fontSize: 14 }}>How the agent is set up, in plain words.</Typography>
      </Box>
      <PageState state={pageState(loading, error, data)} onRetry={() => { setLoading(true); void load(); }}
        skeleton={<Skeleton variant="rounded" height={260} />}>
        {(view) => <SettingsBody view={view} />}
      </PageState>
    </Stack>
  );
}
