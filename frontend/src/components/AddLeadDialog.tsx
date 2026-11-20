import { useState } from "react";
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Checkbox, Stack,
  TextField } from "@mui/material";
import { addLead, errorText } from "../api";
import { isAdded, validateNewLead } from "../lib/leads";
import type { NewLead } from "../types";
import OutcomeChip from "./OutcomeChip";

interface Props {
  open: boolean;
  onClose: () => void;
  onAdded: () => void;
}

const EMPTY = { name: "", company: "", role: "", email: "", phone: "", country: "" };
type Form = typeof EMPTY;

const clean = (f: Form, consent: boolean): NewLead => {
  const out: NewLead = { name: f.name.trim(), company: f.company.trim(), consent_whatsapp: consent };
  for (const k of ["role", "email", "phone", "country"] as const) {
    const v = f[k].trim();
    if (v) out[k] = v;
  }
  return out;
};

export default function AddLeadDialog({ open, onClose, onAdded }: Props) {
  const [form, setForm] = useState<Form>(EMPTY);
  const [consent, setConsent] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [refused, setRefused] = useState<{ outcome: string; reason?: string | null } | null>(null);

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm((f) => ({ ...f, [k]: e.target.value }));
    setErrors((er) => ({ ...er, [k]: "", ...(k === "phone" ? { email: "" } : {}) }));
  };

  const close = () => {
    if (busy) return;
    setForm(EMPTY);
    setConsent(false);
    setErrors({});
    setFailure(null);
    setRefused(null);
    onClose();
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const lead = clean(form, consent);
    const found = validateNewLead(lead);
    setErrors(found);
    setFailure(null);
    setRefused(null);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      const out = await addLead(lead);
      if (out.error) throw new Error(out.error);
      if (isAdded(out.outcome)) {
        onAdded();
        close();
        return;
      }
      setRefused({ outcome: out.outcome, reason: out.reason });
    } catch (err) {
      setFailure(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const field = (k: keyof Form, label: string, extra: object = {}) => (
    <TextField label={label} value={form[k]} onChange={set(k)} error={Boolean(errors[k])}
      helperText={errors[k] || (extra as { hint?: string }).hint} fullWidth size="small" InputLabelProps={{ shrink: true }}
      {...extra} />
  );

  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="sm" PaperProps={{ component: "form", onSubmit: submit, noValidate: true }}>
      <DialogTitle>Add a lead</DialogTitle>
      <DialogContent>
        <Stack spacing={2.25} sx={{ pt: 1 }}>
          {failure && <Alert severity="error">{failure}</Alert>}
          {refused && (
            <Alert severity="warning" icon={false}>
              <OutcomeChip outcome={refused.outcome} />
              {refused.reason ? ` Reason: ${refused.reason}.` : ""}
            </Alert>
          )}
          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
            {field("name", "Name", { required: true, autoComplete: "off", autoFocus: true })}
            {field("company", "Company", { required: true })}
            {field("role", "Role", { placeholder: "Head of Collections" })}
            {field("email", "Email", { type: "email" })}
            {field("phone", "Phone", { type: "tel", placeholder: "+91 98000 00000" })}
            {field("country", "Country", { placeholder: "IN" })}
          </Box>
          <FormControlLabel control={<Checkbox checked={consent} onChange={(e) => setConsent(e.target.checked)} />}
            label="WhatsApp opt-in: the lead agreed to get WhatsApp messages" />
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button color="inherit" onClick={close} disabled={busy}>Cancel</Button>
        <Button type="submit" variant="contained" disabled={busy}>{busy ? "Adding" : "Add lead"}</Button>
      </DialogActions>
    </Dialog>
  );
}
