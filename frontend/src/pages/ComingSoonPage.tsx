import { Box, Typography } from "@mui/material";

/** Placeholder route until the real page lands. */
export default function ComingSoonPage({ title }: { title: string }) {
  return (
    <Box sx={{ py: 8 }}>
      <Typography variant="h5" gutterBottom>{title}</Typography>
      <Typography color="text.secondary">This page is coming soon.</Typography>
    </Box>
  );
}
