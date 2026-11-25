import { useState, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import {
  AppBar, Avatar, Box, Drawer, IconButton, List, ListItemButton, ListItemIcon, ListItemText, Toolbar, Tooltip,
  Typography, useMediaQuery, useTheme,
} from "@mui/material";
import CampaignRounded from "@mui/icons-material/CampaignRounded";
import GroupRounded from "@mui/icons-material/GroupRounded";
import ForumRounded from "@mui/icons-material/ForumRounded";
import DescriptionRounded from "@mui/icons-material/DescriptionRounded";
import ExtensionRounded from "@mui/icons-material/ExtensionRounded";
import SettingsRounded from "@mui/icons-material/SettingsRounded";
import MenuRounded from "@mui/icons-material/MenuRounded";
import { useUser } from "./user";
import CampaignPage from "./pages/CampaignPage";
import TemplatesPage from "./pages/TemplatesPage";
import ConversationsPage from "./pages/ConversationsPage";
import ConnectorsPage from "./pages/ConnectorsPage";
import SettingsPage from "./pages/SettingsPage";
import LeadsPage from "./pages/LeadsPage";

const NAV_WIDTH = 232;
const SIDEBAR_BG = "#16181b";
const SELECTED_BG = "#20302f";
const SELECTED_TEXT = "#5eead4";

export const NAV: { to: string; label: string; icon: ReactNode; group: "main" | "workspace" }[] = [
  { to: "/campaign", label: "Campaign", icon: <CampaignRounded />, group: "main" },
  { to: "/leads", label: "Leads", icon: <GroupRounded />, group: "main" },
  { to: "/conversations", label: "Conversations", icon: <ForumRounded />, group: "main" },
  { to: "/templates", label: "Templates", icon: <DescriptionRounded />, group: "main" },
  { to: "/connectors", label: "Connectors", icon: <ExtensionRounded />, group: "workspace" },
  { to: "/settings", label: "Settings", icon: <SettingsRounded />, group: "workspace" },
];

function NavItem({ item, onPick }: { item: (typeof NAV)[number]; onPick: () => void }) {
  return (
    <ListItemButton
      component={NavLink}
      to={item.to}
      onClick={onPick}
      sx={{
        borderRadius: 2,
        mb: 0.5,
        color: "text.secondary",
        "&.active": { bgcolor: SELECTED_BG, color: SELECTED_TEXT },
        "&.active .MuiListItemIcon-root": { color: SELECTED_TEXT },
      }}
    >
      <ListItemIcon sx={{ minWidth: 36, color: "inherit" }}>{item.icon}</ListItemIcon>
      <ListItemText primary={item.label} primaryTypographyProps={{ fontWeight: 600, fontSize: 14 }} />
    </ListItemButton>
  );
}

function Nav({ onPick }: { onPick: () => void }) {
  return (
    <List sx={{ px: 1.5, pt: 1 }}>
      {NAV.filter((n) => n.group === "main").map((item) => <NavItem key={item.to} item={item} onPick={onPick} />)}
      <Typography variant="overline" color="text.secondary" sx={{ display: "block", px: 1.5, pt: 2, pb: 0.5 }}>
        Workspace
      </Typography>
      {NAV.filter((n) => n.group === "workspace").map((item) => <NavItem key={item.to} item={item} onPick={onPick} />)}
    </List>
  );
}

export default function App() {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("md"));
  const [open, setOpen] = useState(false);
  const { name, askName } = useUser();

  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <AppBar position="fixed" color="inherit" elevation={0}
        sx={{ zIndex: (t) => t.zIndex.drawer + 1, borderBottom: 1, borderColor: "divider", bgcolor: "background.default" }}>
        <Toolbar sx={{ gap: 1.5 }}>
          {!wide && (
            <IconButton edge="start" onClick={() => setOpen(true)} aria-label="Open navigation">
              <MenuRounded />
            </IconButton>
          )}
          <Box sx={{ width: 30, height: 30, borderRadius: 2, bgcolor: "primary.main", color: "primary.contrastText",
            display: "grid", placeItems: "center", fontWeight: 800, fontSize: 15 }}>O</Box>
          <Typography variant="h6" sx={{ flexGrow: 1, fontSize: 17 }}>Ozoo Outreach</Typography>
          <Tooltip title={`Changes are recorded as ${name}. Click to change.`}>
            <IconButton onClick={askName} size="small" aria-label="Change your name">
              <Avatar sx={{ width: 32, height: 32, bgcolor: "primary.main", color: "primary.contrastText", fontSize: 14 }}>
                {name.slice(0, 2).toUpperCase()}
              </Avatar>
            </IconButton>
          </Tooltip>
        </Toolbar>
      </AppBar>
      <Drawer
        variant={wide ? "permanent" : "temporary"}
        open={wide || open}
        onClose={() => setOpen(false)}
        sx={{ width: NAV_WIDTH, flexShrink: 0,
          "& .MuiDrawer-paper": { width: NAV_WIDTH, boxSizing: "border-box", bgcolor: SIDEBAR_BG, border: 0,
            borderRight: 1, borderColor: "divider", borderRadius: 0 } }}
      >
        <Toolbar />
        <Nav onPick={() => setOpen(false)} />
      </Drawer>
      <Box component="main" sx={{ flexGrow: 1, minWidth: 0, p: { xs: 2, md: 3 } }}>
        <Toolbar />
        <Routes>
          <Route path="/" element={<Navigate to="/campaign" replace />} />
          <Route path="/campaign" element={<CampaignPage />} />
          <Route path="/leads" element={<LeadsPage />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/templates" element={<TemplatesPage />} />
          <Route path="/connectors" element={<ConnectorsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/campaign" replace />} />
        </Routes>
      </Box>
    </Box>
  );
}
