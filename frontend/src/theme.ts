import { createTheme } from "@mui/material/styles";

const DIVIDER = "#2a2e34";

export const theme = createTheme({
  palette: {
    mode: "dark",
    primary: { main: "#2dd4bf", contrastText: "#0b1f1d" },
    success: { main: "#4ade80" },
    warning: { main: "#fbbf24" },
    error: { main: "#f87171" },
    background: { default: "#121417", paper: "#1a1d21" },
    text: { primary: "#e7e9ec", secondary: "#9aa1ab" },
    divider: DIVIDER,
  },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: '"Plus Jakarta Sans", "Roboto", "Helvetica", "Arial", sans-serif',
    h5: { fontWeight: 700, letterSpacing: "-0.01em" },
    h6: { fontWeight: 650 },
    subtitle2: { fontWeight: 600 },
    button: { textTransform: "none", fontWeight: 600 },
  },
  components: {
    MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { borderRadius: 8 } } },
    MuiCard: {
      defaultProps: { variant: "outlined" },
      styleOverrides: { root: { borderRadius: 12, borderColor: DIVIDER, backgroundImage: "none" } },
    },
    MuiPaper: {
      defaultProps: { variant: "outlined" },
      styleOverrides: { root: { borderRadius: 12, borderColor: DIVIDER, backgroundImage: "none" } },
    },
    MuiChip: { styleOverrides: { root: { fontWeight: 500, borderRadius: 999 } } },
    MuiTableCell: { styleOverrides: { head: { fontWeight: 600, color: "#9aa1ab" } } },
    MuiDrawer: {
      styleOverrides: {
        paper: { backgroundColor: "#1a1d21", backgroundImage: "none" },
        // Side panels (lead, email, schedule) open above the app bar, which sits at drawer + 1.
        modal: ({ ownerState }) => (ownerState.anchor === "right" ? { zIndex: 1300 } : {}),
      },
    },
    MuiAppBar: { styleOverrides: { root: { backgroundImage: "none" } } },
  },
});
