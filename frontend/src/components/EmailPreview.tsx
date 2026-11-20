import { useState } from "react";
import { Box, Paper, Tab, Tabs, Typography } from "@mui/material";

/** The email as the lead sees it: designed HTML in a sandboxed frame (no scripts, no same-origin), or plain text. */
export default function EmailPreview({ subject, html, text, height = 560 }:
  { subject: string; html: string; text: string; height?: number | string }) {
  const [tab, setTab] = useState<"html" | "text">(html ? "html" : "text");
  const showing = html ? tab : "text";
  return (
    <Paper variant="outlined" sx={{ overflow: "hidden" }}>
      <Box sx={{ px: 2, py: 1.25, bgcolor: "#f9fafb", borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="caption" color="text.secondary">Subject</Typography>
        <Typography sx={{ fontWeight: 600, fontSize: 15 }}>{subject || "(no subject)"}</Typography>
      </Box>
      <Tabs value={showing} onChange={(_, v) => setTab(v)} sx={{ px: 1, minHeight: 40, borderBottom: 1, borderColor: "divider",
        "& .MuiTab-root": { minHeight: 40 } }}>
        <Tab value="html" label="Designed" disabled={!html} />
        <Tab value="text" label="Plain text" />
      </Tabs>
      {showing === "html" ? (
        <Box component="iframe" title="Email preview" sandbox="" srcDoc={html}
          sx={{ display: "block", width: "100%", height, border: 0, bgcolor: "#fff" }} />
      ) : (
        <Box component="pre" sx={{ m: 0, p: 2, whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "inherit",
          fontSize: 14, lineHeight: 1.6, height, overflow: "auto" }}>
          {text}
        </Box>
      )}
    </Paper>
  );
}
