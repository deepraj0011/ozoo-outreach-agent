import { useEffect, useRef, useState } from "react";
import { Alert, Box, Button, Card, Chip, Stack, TextField, Tooltip, Typography } from "@mui/material";
import { errorText, postJson } from "../api";
import type { Rendered, Template, TemplateStatus } from "../types";
import { useUser } from "../user";
import EmailPreview from "./EmailPreview";
import { TEMPLATE_STATUS } from "./templateStatus";

const PREVIEW_DELAY_MS = 500;
type Field = "subject" | "body";

interface Props {
  template: Template | null; // null = new
  placeholders: string[];
  onSaved: (message: string, id: number) => void;
}

/** The right pane of the Templates page: edit one template, insert placeholders, see the rendered email. */
export default function TemplateEditor({ template, placeholders, onSaved }: Props) {
  const { name: by } = useUser();
  const [name, setName] = useState(template?.name ?? "");
  const [subject, setSubject] = useState(template?.subject ?? "");
  const [body, setBody] = useState(template?.body ?? "");
  const [preview, setPreview] = useState<Rendered | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const lastField = useRef<Field>("body");

  // The page remounts this editor (key) when another template is picked; a save of the same one refreshes the fields.
  useEffect(() => {
    setName(template?.name ?? "");
    setSubject(template?.subject ?? "");
    setBody(template?.body ?? "");
    setError(null);
  }, [template]);

  useEffect(() => {
    if (!name.trim() || !subject.trim() || !body.trim()) {
      setPreview(null);
      setPreviewError("Fill in the name, subject and text to see the preview.");
      return undefined;
    }
    let live = true;
    const t = window.setTimeout(() => {
      postJson<Rendered>("/demo/api/p0/preview", { draft: { name, subject, body } })
        .then((r) => {
          if (!live) return;
          setPreview(r);
          setPreviewError(null);
        })
        .catch((e) => live && setPreviewError(errorText(e)));
    }, PREVIEW_DELAY_MS);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [name, subject, body]);

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
      onSaved(status === "live" ? "Template saved and live: it can now go on the schedule." : "Template saved.", out.id);
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
      onSaved("Template switched off.", template.id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const isLive = template?.status === "live";
  const look = template ? TEMPLATE_STATUS[template.status] : null;
  return (
    <Card sx={{ minWidth: 0 }}>
      <Stack direction="row" alignItems="center" spacing={1.5} useFlexGap flexWrap="wrap"
        sx={{ px: 2.5, py: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="h6" sx={{ fontSize: 16, flexGrow: 1 }} noWrap>
          {template ? template.name : "New template"}
        </Typography>
        {look && <Chip size="small" label={look.label} sx={{ ...look.sx, fontWeight: 600 }} />}
        {template && template.status !== "off" && (
          <Button color="error" onClick={switchOff} disabled={busy}>Switch off</Button>
        )}
        <Button variant={isLive ? "contained" : "outlined"} color={isLive ? "primary" : "inherit"}
          sx={isLive ? undefined : { borderColor: "divider" }} onClick={() => save()} disabled={busy}>
          {template ? "Save changes" : "Save draft"}
        </Button>
        {!isLive && <Button variant="contained" onClick={() => save("live")} disabled={busy}>Save and make live</Button>}
      </Stack>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", xl: "1fr 1fr" }, gap: 3, p: 2.5 }}>
        <Stack spacing={2} sx={{ minWidth: 0 }}>
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
            minRows={12} inputRef={bodyRef} onFocus={() => { lastField.current = "body"; }}
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
        <Stack spacing={1.5} sx={{ minWidth: 0 }}>
          <Typography variant="subtitle2">Preview (a sample lead, Head of Collections)</Typography>
          {previewError && <Alert severity="warning">{previewError}</Alert>}
          {preview && !previewError && (
            <EmailPreview subject={preview.subject} html={preview.html} text={preview.text} height={520} />
          )}
        </Stack>
      </Box>
    </Card>
  );
}
