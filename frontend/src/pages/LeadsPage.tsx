import { useCallback, useEffect, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Avatar, Box, Button, Card, Chip, InputAdornment, Skeleton, Snackbar, Stack, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from "@mui/material";
import InboxRounded from "@mui/icons-material/InboxRounded";
import PersonAddRounded from "@mui/icons-material/PersonAddRounded";
import SearchRounded from "@mui/icons-material/SearchRounded";
import UploadFileRounded from "@mui/icons-material/UploadFileRounded";
import { errorText, getBoard } from "../api";
import { initials, matchesQuery } from "../lib/campaign";
import { pageState } from "../lib/pageState";
import type { Board } from "../types";
import AddLeadDialog from "../components/AddLeadDialog";
import ImportDialog from "../components/ImportDialog";
import PageState from "../components/PageState";
import { leadStatus, TONE_SX } from "../components/status";

function PageSkeleton() {
  return (
    <Stack spacing={2.5}>
      <Skeleton variant="rounded" height={40} width={280} />
      <Skeleton variant="rounded" height={360} />
    </Stack>
  );
}

function Empty({ onAdd, onImport }: { onAdd: () => void; onImport: () => void }) {
  return (
    <Box sx={{ py: 7, px: 3, textAlign: "center" }}>
      <Box sx={{ width: 56, height: 56, mx: "auto", borderRadius: 3, display: "grid", placeItems: "center",
        bgcolor: "#1d2e2c", color: "primary.main" }}>
        <InboxRounded sx={{ fontSize: 30 }} />
      </Box>
      <Typography variant="h6" sx={{ mt: 2, fontSize: 17 }}>No leads yet</Typography>
      <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2.5, fontSize: 14 }}>
        Add one lead, or import a CSV or Excel file.
      </Typography>
      <Stack direction="row" spacing={1.5} justifyContent="center">
        <Button variant="contained" startIcon={<PersonAddRounded />} onClick={onAdd}>Add lead</Button>
        <Button variant="outlined" color="inherit" startIcon={<UploadFileRounded />} onClick={onImport}
          sx={{ borderColor: "divider" }}>Import</Button>
      </Stack>
    </Box>
  );
}

export default function LeadsPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setBoard(await getBoard());
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

  const shown = useMemo(() => (board?.leads ?? []).filter((l) => matchesQuery(l, query)), [board, query]);
  const state = pageState(loading, board ? null : error, board);

  return (
    <PageState state={state} onRetry={() => { setLoading(true); void load(); }} skeleton={<PageSkeleton />}>
      {(b) => (
        <Stack spacing={2.5} sx={{ maxWidth: 1100 }}>
          <Stack direction="row" alignItems="center" spacing={1.5} useFlexGap flexWrap="wrap">
            <Box sx={{ flexGrow: 1 }}>
              <Typography variant="h5">Leads</Typography>
              <Typography color="text.secondary" sx={{ fontSize: 14 }}>
                {b.leads.length} in the campaign.{" "}
                <Box component={RouterLink} to="/campaign" sx={{ color: "primary.main" }}>See them on the Campaign board</Box>
              </Typography>
            </Box>
            <Button variant="outlined" color="inherit" startIcon={<UploadFileRounded />} sx={{ borderColor: "divider" }}
              onClick={() => setImporting(true)}>Import</Button>
            <Button variant="contained" startIcon={<PersonAddRounded />} onClick={() => setAdding(true)}>Add lead</Button>
          </Stack>
          {error !== null && <Alert severity="warning">Could not refresh: {errorText(error)}</Alert>}
          <Card>
            {b.leads.length === 0 ? <Empty onAdd={() => setAdding(true)} onImport={() => setImporting(true)} /> : (
              <>
                <Stack direction="row" sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: "divider" }}>
                  <TextField size="small" placeholder="Name or company" value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    sx={{ width: { xs: "100%", md: 280 }, "& .MuiOutlinedInput-root": { bgcolor: "background.default" } }}
                    inputProps={{ "aria-label": "Search by name or company" }}
                    InputProps={{ startAdornment: (
                      <InputAdornment position="start"><SearchRounded fontSize="small" /></InputAdornment>
                    ) }} />
                </Stack>
                <TableContainer sx={{ maxHeight: "calc(100vh - 280px)" }}>
                  <Table stickyHeader size="small"
                    sx={{ "& td, & th": { borderColor: "divider" }, "& th": { bgcolor: "background.paper", py: 1.25 },
                      "& td": { py: 1.25 } }}>
                    <TableHead>
                      <TableRow>
                        <TableCell sx={{ pl: 2 }}>Lead</TableCell>
                        <TableCell>Company</TableCell>
                        <TableCell>Role</TableCell>
                        <TableCell>Status</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {shown.map((l) => {
                        const st = leadStatus(l);
                        return (
                          <TableRow key={l.id} hover>
                            <TableCell sx={{ pl: 2 }}>
                              <Stack direction="row" alignItems="center" spacing={1.25}>
                                <Avatar sx={{ width: 32, height: 32, fontSize: 12, fontWeight: 700, bgcolor: "#1d2e2c",
                                  color: "primary.main" }}>{initials(l.name)}</Avatar>
                                <Typography sx={{ fontWeight: 600, fontSize: 13.5 }}>{l.name}</Typography>
                              </Stack>
                            </TableCell>
                            <TableCell sx={{ fontSize: 13.5 }}>{l.company}</TableCell>
                            <TableCell sx={{ fontSize: 13.5, color: "text.secondary" }}>{l.role}</TableCell>
                            <TableCell>
                              <Chip size="small" label={st.label} sx={{ ...TONE_SX[st.tone], fontWeight: 600, fontSize: 12 }} />
                            </TableCell>
                          </TableRow>
                        );
                      })}
                      {shown.length === 0 && (
                        <TableRow>
                          <TableCell colSpan={4} sx={{ py: 6, textAlign: "center", color: "text.secondary" }}>
                            No leads match this search.
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </TableContainer>
              </>
            )}
          </Card>
          <AddLeadDialog open={adding} onClose={() => setAdding(false)}
            onAdded={() => { setToast("Lead added."); void load(); }} />
          <ImportDialog open={importing} onClose={() => setImporting(false)}
            onImported={(n) => { setToast(`${plural(n)} added.`); void load(); }} />
          <Snackbar open={toast !== null} autoHideDuration={4000} onClose={() => setToast(null)}
            anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
            <Alert severity="success" variant="filled" onClose={() => setToast(null)}>{toast}</Alert>
          </Snackbar>
        </Stack>
      )}
    </PageState>
  );
}

const plural = (n: number) => `${n} lead${n === 1 ? "" : "s"}`;
