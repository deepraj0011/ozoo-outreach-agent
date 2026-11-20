import { useRef, useState } from "react";
import { Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Link, Stack, Table,
  TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import UploadFileRounded from "@mui/icons-material/UploadFileRounded";
import { errorText, importLeads, SAMPLE_CSV_URL } from "../api";
import { importTotals } from "../lib/leads";
import type { ImportPreview, ImportResult } from "../types";
import OutcomeChip from "./OutcomeChip";

interface Props {
  open: boolean;
  onClose: () => void;
  onImported: (added: number) => void;
}

const plural = (n: number) => `${n} lead${n === 1 ? "" : "s"}`;

function PreviewTable({ preview }: { preview: ImportPreview }) {
  const problems = preview.rows.filter((r) => r.problems.length);
  if (!problems.length) return null;
  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Rows with a problem (these are skipped)</Typography>
      <Box sx={{ maxHeight: 260, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 2 }}>
        <Table size="small" stickyHeader>
          <TableHead><TableRow><TableCell>Row</TableCell><TableCell>Name</TableCell><TableCell>Problem</TableCell></TableRow></TableHead>
          <TableBody>
            {problems.map((r) => (
              <TableRow key={r.row}>
                <TableCell>{r.row}</TableCell>
                <TableCell>{r.payload.name ?? ""}</TableCell>
                <TableCell>{r.problems.join(", ")}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}

function ResultTable({ result }: { result: ImportResult }) {
  return (
    <Box sx={{ maxHeight: 300, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 2 }}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow><TableCell>Row</TableCell><TableCell>Name</TableCell><TableCell>Result</TableCell><TableCell>Reason</TableCell></TableRow>
        </TableHead>
        <TableBody>
          {result.results.map((r) => (
            <TableRow key={r.row}>
              <TableCell>{r.row}</TableCell>
              <TableCell>{r.name}</TableCell>
              <TableCell><OutcomeChip outcome={r.outcome} /></TableCell>
              <TableCell>{r.reason ?? ""}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

export default function ImportDialog({ open, onClose, onImported }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState<"reading" | "importing" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setFile(null);
    setPreview(null);
    setResult(null);
    setError(null);
    if (input.current) input.current.value = "";
  };

  const close = () => {
    if (busy) return;
    const added = result ? importTotals(result.summary).added : 0;
    reset();
    onClose();
    if (added) onImported(added);
  };

  const pick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    setPreview(null);
    setResult(null);
    setError(null);
    if (!f) return;
    setBusy("reading");
    try {
      setPreview(await importLeads(f, false));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const commit = async () => {
    if (!file) return;
    setBusy("importing");
    setError(null);
    try {
      const r = await importLeads(file, true);
      setResult(r);
      setPreview(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const totals = result ? importTotals(result.summary) : null;
  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="md">
      <DialogTitle>Import leads</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <Typography color="text.secondary" sx={{ fontSize: 14 }}>
            Columns: Name, Company, Role, Email, Phone, Country, WhatsApp opt-in.{" "}
            <Link href={SAMPLE_CSV_URL} download="leads_template.csv">Download the sample CSV</Link>
          </Typography>
          {!result && (
            <Stack direction="row" spacing={1.5} alignItems="center">
              <Button variant="outlined" color="inherit" startIcon={<UploadFileRounded />} sx={{ borderColor: "divider" }}
                onClick={() => input.current?.click()} disabled={busy !== null}>
                {file ? "Choose another file" : "Choose a file"}
              </Button>
              <Typography noWrap sx={{ fontSize: 14, color: "text.secondary" }}>{file?.name ?? "CSV or Excel, up to 5 MB"}</Typography>
              <input ref={input} type="file" hidden accept=".csv,.xlsx,.xlsm,.txt" onChange={pick}
                aria-label="Lead file" data-testid="lead-file" />
            </Stack>
          )}
          {busy && (
            <Stack direction="row" spacing={1.5} alignItems="center">
              <CircularProgress size={18} />
              <Typography sx={{ fontSize: 14 }}>{busy === "reading" ? "Reading the file" : "Importing leads"}</Typography>
            </Stack>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          {preview && (
            <>
              <Alert severity={preview.ready ? "success" : "warning"} icon={false}>
                {preview.total} rows, {preview.ready} ready to import.
              </Alert>
              <PreviewTable preview={preview} />
            </>
          )}
          {result && totals && (
            <>
              <Alert severity="success">
                {totals.added} added, {totals.duplicates} duplicates, {totals.rejected} rejected.
              </Alert>
              <ResultTable result={result} />
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button color="inherit" onClick={close} disabled={busy !== null}>{result ? "Done" : "Cancel"}</Button>
        {preview && (
          <Button variant="contained" onClick={commit} disabled={busy !== null || preview.ready === 0}>
            Import {plural(preview.ready)}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
