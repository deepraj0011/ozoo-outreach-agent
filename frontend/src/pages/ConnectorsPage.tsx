import { useCallback, useEffect, useState } from "react";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import CheckCircleRounded from "@mui/icons-material/CheckCircleRounded";
import ErrorRounded from "@mui/icons-material/ErrorRounded";
import LinkOffRounded from "@mui/icons-material/LinkOffRounded";
import { getConnectors } from "../api";
import { GROUPS, summarize } from "../lib/connectors";
import { pageState } from "../lib/pageState";
import type { Connector } from "../types";
import ConnectorCard from "../components/ConnectorCard";
import ConnectorDrawer from "../components/ConnectorDrawer";
import PageState from "../components/PageState";

function SummaryChip({ icon, count, label }: { icon: JSX.Element; count: number; label: string }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}
      sx={{ px: 1.5, py: 1, borderRadius: 2.5, bgcolor: "background.paper", border: 1, borderColor: "divider" }}>
      {icon}
      <Typography sx={{ fontWeight: 700, fontSize: 16 }}>{count}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600 }}>{label}</Typography>
    </Stack>
  );
}

function PageSkeleton() {
  return (
    <Stack spacing={2}>
      <Skeleton variant="rounded" width={240} height={36} />
      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: "repeat(3, 1fr)" } }}>
        {[0, 1, 2].map((i) => <Skeleton key={i} variant="rounded" height={150} />)}
      </Box>
    </Stack>
  );
}

function Groups({ connectors, onOpen }: { connectors: Connector[]; onOpen: (c: Connector) => void }) {
  return (
    <Stack spacing={3}>
      {GROUPS.map((g) => {
        const items = connectors.filter((c) => c.group === g.id);
        if (items.length === 0) return null;
        return (
          <Box key={g.id} component="section">
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>{g.title}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>{g.blurb}</Typography>
            <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: "repeat(3, 1fr)" } }}>
              {items.map((c) => <ConnectorCard key={c.id} connector={c} onOpen={onOpen} />)}
            </Box>
          </Box>
        );
      })}
    </Stack>
  );
}

export default function ConnectorsPage() {
  const [data, setData] = useState<Connector[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<Connector | null>(null);

  const load = useCallback(async () => {
    try {
      setData((await getConnectors()).connectors);
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
    <PageState state={pageState(loading, error, data)} onRetry={() => { setLoading(true); void load(); }}
      skeleton={<PageSkeleton />}>
      {(connectors) => {
        const sum = summarize(connectors);
        return (
          <Stack spacing={3}>
            <Stack direction="row" alignItems="center" spacing={2} useFlexGap flexWrap="wrap">
              <Box sx={{ flexGrow: 1 }}>
                <Typography variant="h5">Connectors</Typography>
                <Typography color="text.secondary" sx={{ fontSize: 14 }}>The tools the agent sends, books and alerts with.</Typography>
              </Box>
              <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                <SummaryChip icon={<CheckCircleRounded fontSize="small" color="success" />} count={sum.active} label="active" />
                <SummaryChip icon={<ErrorRounded fontSize="small" color="warning" />} count={sum.attention} label="need a look" />
                <SummaryChip icon={<LinkOffRounded fontSize="small" sx={{ color: "text.secondary" }} />} count={sum.off} label="not in use" />
              </Stack>
            </Stack>
            <Groups connectors={connectors} onOpen={setOpen} />
            <ConnectorDrawer connector={open} onClose={() => setOpen(null)} />
          </Stack>
        );
      }}
    </PageState>
  );
}
