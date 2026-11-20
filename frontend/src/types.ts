export type CellStatus = "sent" | "planned" | "today" | "handed" | "cancelled" | "skipped";
export type StopReason = "replied" | "unsubscribed" | "bounced" | "booked" | "stopped";
export type TemplateStatus = "draft" | "live" | "off";

export interface Cell {
  email_no: number;
  status: CellStatus;
  date: string | null;
  sent_at: string | null;
  opens: number;
  clicks: number;
  visits: number;
  reason: string | null;
  late: boolean;
}

export interface BoardLead {
  id: number;
  name: string;
  company: string;
  role: string;
  state: string;
  paused: boolean;
  stop_reason: StopReason | null;
  stopped_at: string | null;
  emails: Cell[];
}

export interface ScheduleRow {
  email_no: number;
  day: number;
  template_id: number;
  template_name: string;
  mode: "now" | "day" | "at";
  at: string | null;
}

export interface TemplateRef {
  id: number;
  name: string;
  status: TemplateStatus;
}

export interface Totals {
  leads: number;
  sent: number;
  sent_today: number;
  today: number;
  replied: number;
  stopped: number;
  unsubscribed: number;
}

export interface ExampleDate {
  email_no: number;
  day: number | string | null;
  date: string;
  mode?: ScheduleRow["mode"];
  when?: string;
  exact?: boolean;
}

export interface CampaignState {
  running: boolean;
  started_at: string | null;
  leads: number;
}

export interface Board {
  campaign: CampaignState;
  schedule: ScheduleRow[];
  templates: TemplateRef[];
  totals: Totals;
  leads: BoardLead[];
  now: string;
  tz: string;
  example: ExampleDate[];
}

export interface EmailDetail {
  email_no: number;
  subject: string;
  text: string;
  html: string;
  html_is_current_template: boolean;
  template_name: string;
  status: Exclude<CellStatus, "today">;
  reason: string | null;
  when: string | null;
  sent_at: string | null;
  opens: number;
  clicks: number;
  visits: number;
}

export interface Template {
  id: number;
  name: string;
  subject: string;
  body: string;
  status: TemplateStatus;
  used_by: number[];
  updated_at: string;
  updated_by: string;
}

export interface TemplateList {
  templates: Template[];
  placeholders: string[];
}

export interface Rendered {
  subject: string;
  text: string;
  html: string;
}

export type ConnectorStatus = "active" | "attention" | "available" | "coming_soon";

export interface Connector {
  id: string;
  group: "email" | "calendar" | "messaging";
  name: string;
  account: string;
  status: ConnectorStatus;
  detail: string;
  basis: string;
  setup: { steps: string[]; env_keys: string[] };
}

export type SettingValue = string | boolean | number | string[];

export interface SettingRow {
  label: string;
  help: string;
  value: SettingValue;
}

export interface SettingsSection {
  id: string;
  title: string;
  rows: SettingRow[];
}

export interface SettingsView {
  sections: SettingsSection[];
}

export interface NewLead {
  name: string;
  company: string;
  role?: string;
  email?: string;
  phone?: string;
  country?: string;
  consent_whatsapp?: boolean;
}

export interface ImportRow {
  row: number;
  payload: { name?: string; company?: string; role?: string; email?: string; phone?: string };
  problems: string[];
}

export interface ImportPreview {
  columns: Record<string, string>;
  total: number;
  ready: number;
  rows: ImportRow[];
}

export interface IntakeResult {
  outcome: string;
  lead_id?: number | null;
  reason?: string | null;
  error?: string;
}

export interface ImportResultRow {
  row: number;
  name: string;
  lead_id?: number | null;
  outcome: string;
  reason?: string | null;
}

export interface ImportResult {
  summary: Record<string, number>;
  results: ImportResultRow[];
}
