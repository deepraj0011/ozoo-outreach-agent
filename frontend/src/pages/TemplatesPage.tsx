import { useCallback, useEffect, useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, Paper, Snackbar, Stack, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Typography } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import { errorText, getJson } from "../api";
import { dateTime } from "../format";
import type { Template, TemplateList, TemplateStatus } from "../types";
import TemplateEditor from "../components/TemplateEditor";

const STATUS: Record<TemplateStatus, { label: string; color: "success" | "default" | "warning" }> = {
  live: { label: "Live", color: "success" },
  draft: { label: "Draft", color: "warning" },
  off: { label: "Off", color: "default" },
};

export default function TemplatesPage() {
  const [data, setData] = useState<TemplateList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Template | null>(null);
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getJson<TemplateList>("/demo/api/p0/templates"));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const edit = (t: Template | null) => {
    setEditing(t);
    setOpen(true);
  };

  return (
    <Stack spacing={3}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-end">
        <Box>
          <Typography variant="h5">Templates</Typography>
          <Typography color="text.secondary" sx={{ fontSize: 14 }}>
            The emails you write. Only live templates can go on the schedule.
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => edit(null)}>New template</Button>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {!data && !error && <Box sx={{ py: 8, textAlign: "center" }}><CircularProgress /></Box>}
      {data && (
        <TableContainer component={Paper} variant="outlined">
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Template</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>On the schedule</TableCell>
                <TableCell>Last change</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.templates.map((t) => (
                <TableRow key={t.id} hover onClick={() => edit(t)} sx={{ cursor: "pointer" }}>
                  <TableCell>
                    <Typography sx={{ fontWeight: 600, fontSize: 14 }}>{t.name}</Typography>
                    <Typography noWrap sx={{ fontSize: 12, color: "text.secondary", maxWidth: 420 }}>{t.subject}</Typography>
                  </TableCell>
                  <TableCell>
                    <Chip size="small" label={STATUS[t.status].label} color={STATUS[t.status].color} />
                  </TableCell>
                  <TableCell sx={{ fontSize: 13 }}>
                    {t.used_by.length ? t.used_by.map((n) => `Email ${n}`).join(", ") : (
                      <Typography component="span" sx={{ fontSize: 13, color: "text.disabled" }}>Not used</Typography>
                    )}
                  </TableCell>
                  <TableCell sx={{ fontSize: 13, color: "text.secondary" }}>
                    {dateTime(t.updated_at)} · {t.updated_by}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <TemplateEditor open={open} template={editing} placeholders={data?.placeholders ?? []}
        onClose={() => setOpen(false)}
        onSaved={(m) => {
          setOpen(false);
          setToast(m);
          void load();
        }} />
      <Snackbar open={toast !== null} autoHideDuration={4000} onClose={() => setToast(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setToast(null)}>{toast}</Alert>
      </Snackbar>
    </Stack>
  );
}
