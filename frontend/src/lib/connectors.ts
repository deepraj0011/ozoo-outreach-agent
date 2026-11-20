import type { Connector, ConnectorStatus } from "../types";

export function summarize(cs: Connector[]): { active: number; attention: number; off: number } {
  return {
    active: cs.filter((c) => c.status === "active").length,
    attention: cs.filter((c) => c.status === "attention").length,
    off: cs.filter((c) => c.status === "available" || c.status === "coming_soon").length,
  };
}

export function statusLook(s: ConnectorStatus): { label: string; tone: "success" | "warning" | "muted" } {
  switch (s) {
    case "active": return { label: "Active", tone: "success" };
    case "attention": return { label: "Needs a look", tone: "warning" };
    case "available": return { label: "Not in use", tone: "muted" };
    case "coming_soon": return { label: "Coming soon", tone: "muted" };
  }
}

export function actionLabel(s: ConnectorStatus): "Manage" | "Fix" | "Set up" | "Coming soon" {
  switch (s) {
    case "active": return "Manage";
    case "attention": return "Fix";
    case "available": return "Set up";
    case "coming_soon": return "Coming soon";
  }
}

export const GROUPS: { id: "email" | "calendar" | "messaging"; title: string; blurb: string }[] = [
  { id: "email", title: "Sending email", blurb: "Where the emails go out from and where replies are read." },
  { id: "calendar", title: "Calendar and booking", blurb: "Where leads book a demo and where demos land." },
  { id: "messaging", title: "Messaging, alerts and AI", blurb: "WhatsApp to leads, alerts to the sales team, the model that reads replies." },
];
