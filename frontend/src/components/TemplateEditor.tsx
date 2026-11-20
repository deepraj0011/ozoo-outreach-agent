import { useEffect, useRef, useState } from "react";
import { Alert, AppBar, Box, Button, Chip, Dialog, IconButton, Stack, TextField, Toolbar, Tooltip,
  Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { errorText, postJson } from "../api";
import type { Rendered, Template, TemplateStatus } from "../types";
import { useUser } from "../user";
import EmailPreview from "./EmailPreview";

const PREVIEW_DELAY_MS = 500;
type Field = "subject" | "body";

interface Props {
  open: boolean;
  template: Template | null; // null = new
  placeholders: string[];
  onClose: () => void;
  onSaved: (message: string) => void;
}

export default function TemplateEditor({ open, template, placeholders, onClose, onSaved }: Props) {
  const { name: by } = useUser();
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState<Rendered | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const lastField = useRef<Field>("body");

  useEffect(() => {
    if (!open) return;
    setName(template?.name ?? "");
    setSubject(template?.subject ?? "");
    setBody(template?.body ?? "");
    setError(null);
    setPreview(null);
  }, [open, template]);

  useEffect(() => {
    if (!open) return undefined;
    if (!name.trim() || !subject.trim() || !body.trim()) {
      setPreviewError("Fill in the name, subject and text to see the preview.");
      return undefined;
    }
    const t = window.setTimeout(() => {
      postJson<Rendered>("/demo/api/p0/preview", { draft: { name, subject, body } })
        .then((r) => {
          setPreview(r);
          setPreviewError(null);
        })
        .catch((e) => setPreviewError(errorText(e)));
    }, PREVIEW_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [open, name, subject, body]);

  const insert = (ph: string) => {
    const token = `{${ph}}`;
    const field = lastField.current;
    const el = field === "subject" ? subjectRef.current : bodyRef.current;
    const value = field === "subject" ? subject : body;
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    const next = value.slice(0, start) + token + value.slice(end);
    if (field === "subject") setSubject(next);
    else setBody(next);
    window.requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + token.length, start + token.length);
    });
  };

  const save = async (status?: TemplateStatus) => {
    setBusy(true);
    setError(null);
    try {
      const out = await postJson<{ id: number }>("/demo/api/p0/templates",
        { ...(template ? { id: template.id } : {}), name, subject, body, by });
      if (status && status !== (template?.status ?? "draft")) {
        await postJson(`/demo/api/p0/templates/${out.id}/status`, { status, by });
      }
      onSaved(status === "live" ? "Template saved and live: it can now go on the schedule." : "Template saved.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const switchOff = async () => {
    if (!template) return;
    setBusy(true);
    setError(null);
    try {
      await postJson(`/demo/api/p0/templates/${template.id}/status`, { status: "off", by });
      onSaved("Template switched off.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const isLive = template?.status === "live";
  return (
    <Dialog fullScreen open={open} onClose={onClose}>
      <AppBar position="sticky" color="inherit" elevation={0} sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Toolbar sx={{ gap: 1 }}>
          <IconButton edge="start" onClick={onClose} aria-label="Close"><CloseIcon /></IconButton>
          <Typography variant="h6" sx={{ flexGrow: 1, fontSize: 17 }} noWrap>
            {template ? `Edit template: ${template.name}` : "New template"}
          </Typography>
          {template && template.status !== "off" && (
            <Button color="error" onClick={switchOff} disabled={busy}>Switch off</Button>
          )}
          <Button variant={isLive ? "contained" : "outlined"} onClick={() => save()} disabled={busy}>
            {template ? "Save changes" : "Save draft"}
          </Button>
          {!isLive && (
            <Button variant="contained" onClick={() => save("live")} disabled={busy}>Save and make live</Button>
          )}
        </Toolbar>
      </AppBar>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, gap: 3, p: { xs: 2, md: 3 },
        bgcolor: "background.default", flexGrow: 1 }}>
        <Stack spacing={2}>
          {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
          {isLive && (
            <Alert severity="info" variant="outlined">
              This template is live. Saved changes apply to every email not yet sent with it.
            </Alert>
          )}
          <TextField label="Template name" value={name} onChange={(e) => setName(e.target.value)} fullWidth
            helperText="Only you see this name." />
          <TextField label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} fullWidth
            inputRef={subjectRef} onFocus={() => { lastField.current = "subject"; }} />
          <TextField label="Email text" value={body} onChange={(e) => setBody(e.target.value)} fullWidth multiline
            minRows={14} inputRef={bodyRef} onFocus={() => { lastField.current = "body"; }}
            helperText="Plain text. An unsubscribe line is added automatically if you leave it out." />
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Insert a placeholder at the cursor</Typography>
            <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1}>
              {placeholders.map((ph) => (
                <Tooltip key={ph} title={`Insert {${ph}}`}>
                  <Chip label={`{${ph}}`} onClick={() => insert(ph)} variant="outlined" color="primary" size="small"
                    sx={{ fontFamily: "monospace" }} />
                </Tooltip>
              ))}
            </Stack>
          </Box>
        </Stack>
        <Stack spacing={1.5}>
          <Typography variant="subtitle2">Preview (a sample lead, Head of Collections)</Typography>
          {previewError && <Alert severity="warning">{previewError}</Alert>}
          {preview && <EmailPreview subject={preview.subject} html={preview.html} text={preview.text} height="65vh" />}
        </Stack>
      </Box>
    </Dialog>
  );
}
