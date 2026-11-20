import { Box, Button, Typography } from "@mui/material";
import ForumRounded from "@mui/icons-material/ForumRounded";
import OpenInNewRounded from "@mui/icons-material/OpenInNewRounded";

export default function ConversationsPage() {
  return (
    <Box sx={{ textAlign: "center", py: 10 }}>
      <ForumRounded sx={{ fontSize: 40, color: "text.secondary", mb: 1 }} />
      <Typography variant="h6" gutterBottom>WhatsApp conversations are in the live console for now</Typography>
      <Button variant="outlined" endIcon={<OpenInNewRounded />} href="/demo" target="_blank" rel="noopener noreferrer">
        Open the live console
      </Button>
    </Box>
  );
}
