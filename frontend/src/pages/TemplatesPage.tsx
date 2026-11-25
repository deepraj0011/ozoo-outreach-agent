import { useCallback, useEffect, useState } from "react";
import { Alert, Box, Button, Card, Chip, List, ListItemButton, Skeleton, Snackbar, Stack, Typography } from "@mui/material";
import AddRounded from "@mui/icons-material/AddRounded";
import { errorText, getJson } from "../api";
import { pageState } from "../lib/pageState";
import type { Template, TemplateList } from "../types";
import PageState from "../components/PageState";
import TemplateEditor from "../components/TemplateEditor";
import { TEMPLATE_STATUS, usedByText } from "../components/templateStatus";

const LIST_WIDTH = 340;

function TemplateRow({ t, selected, onPick }: { t: Template; selected: boolean; onPick: () => void }) {
  const look = TEMPLATE_STATUS[t.status];
  return (
    <ListItemButton selected={selected} onClick={onPick}
      sx={{ display: "block", borderRadius: 2, mb: 0.5, py: 1.25,
        "&.Mui-selected": { bgcolor: "#20302f" }, "&.Mui-selected:hover": { bgcolor: "#20302f" } }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography noWrap sx={{ fontWeight: 600, fontSize: 14, flexGrow: 1 }}>{t.name}</Typography>
        <Chip size="small" label={look.label} sx={{ ...look.sx, fontWeight: 600, fontSize: 11.5, height: 22 }} />
      </Stack>
      <Typography noWrap sx={{ fontSize: 12.5, color: "text.secondary", mt: 0.25 }}>{t.subject}</Typography>
      <Typography sx={{ fontSize: 12, color: t.used_by.length ? "primary.main" : "text.disabled", mt: 0.25 }}>
        {usedByText(t.used_by)}
      </Typography>
    </ListItemButton>
  );
}

function PageSkeleton() {
  return (
    <Stack spacing={2.5}>
      <Skeleton variant="rounded" height={40} width={280} />
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: `${LIST_WIDTH}px 1fr` } }}>
        <Skeleton variant="rounded" height={420} />
        <Skeleton variant="rounded" height={420} />
      </Box>
    </Stack>
  );
}

export default function TemplatesPage() {
  const [data, setData] = useState<TemplateList | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<number | "new" | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getJson<TemplateList>("/demo/api/p0/templates"));
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

  const state = pageState(loading, data ? null : error, data);

  return (
    <PageState state={state} onRetry={() => { setLoading(true); void load(); }} skeleton={<PageSkeleton />}>
      {(d) => {
        const current = picked === "new" ? null : d.templates.find((t) => t.id === (picked ?? d.templates[0]?.id)) ?? null;
        const isNew = picked === "new" || d.templates.length === 0;
        return (
          <Stack spacing={2.5} sx={{ maxWidth: 1500 }}>
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Box sx={{ flexGrow: 1 }}>
                <Typography variant="h5">Templates</Typography>
                <Typography color="text.secondary" sx={{ fontSize: 14 }}>
                  The emails you write. Only live templates can go on the schedule.
                </Typography>
              </Box>
              <Button variant="contained" startIcon={<AddRounded />} onClick={() => setPicked("new")}>
                New template
              </Button>
            </Stack>
            {error !== null && <Alert severity="warning">Could not refresh: {errorText(error)}</Alert>}
            <Box sx={{ display: "grid", gap: 2, alignItems: "start",
              gridTemplateColumns: { xs: "1fr", md: `${LIST_WIDTH}px minmax(0, 1fr)` } }}>
              <Card sx={{ p: 1 }}>
                <List disablePadding aria-label="Templates">
                  {d.templates.map((t) => (
                    <TemplateRow key={t.id} t={t} selected={!isNew && current?.id === t.id} onPick={() => setPicked(t.id)} />
                  ))}
                  {d.templates.length === 0 && (
                    <Typography color="text.secondary" sx={{ p: 2, fontSize: 14 }}>No templates yet.</Typography>
                  )}
                </List>
              </Card>
              <TemplateEditor key={isNew ? "new" : current?.id} template={isNew ? null : current}
                placeholders={d.placeholders}
                onSaved={(m, id) => {
                  setToast(m);
                  setPicked(id);
                  void load();
                }} />
            </Box>
            <Snackbar open={toast !== null} autoHideDuration={4000} onClose={() => setToast(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
              <Alert severity="success" variant="filled" onClose={() => setToast(null)}>{toast}</Alert>
            </Snackbar>
          </Stack>
        );
      }}
    </PageState>
  );
}
