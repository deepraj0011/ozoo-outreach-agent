import type { Board, Connector, ImportPreview, ImportResult, IntakeResult, NewLead, SettingsView } from "./types";

/** Small typed client for the agent's local JSON routes (/demo/api/...). */

export class ApiError extends Error {}

function messageOf(body: unknown, status: number): string {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.error === "string") return b.error;
    if (typeof b.detail === "string") return b.detail;
    if (Array.isArray(b.detail)) return "Some fields are not valid: please check them.";
  }
  if (status === 404) return "Not found (is the agent running locally?).";
  return `The request failed (HTTP ${status}).`;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError("Cannot reach the agent. Is it running?");
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) throw new ApiError(messageOf(body, res.status));
  return body as T;
}

export function getJson<T>(url: string): Promise<T> {
  return request<T>(url, { headers: { Accept: "application/json" } });
}

export function postJson<T>(url: string, data: unknown): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(data),
  });
}

export function postFile<T>(url: string, file: File): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "X-Filename": file.name, "Content-Type": "application/octet-stream" },
    body: file,
  });
}

export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong.";
}

const BASE = "/demo/api";

export const SAMPLE_CSV_URL = `${BASE}/sample.csv`;

export const getBoard = () => getJson<Board>(`${BASE}/p0/board`);
export const startCampaign = (by: string) => postJson<unknown>(`${BASE}/p0/campaign/start`, { by });
export const pauseCampaign = (by: string) => postJson<unknown>(`${BASE}/p0/campaign/pause`, { by });
export const getConnectors = () => getJson<{ connectors: Connector[] }>(`${BASE}/connectors`);
export const getSettings = () => getJson<SettingsView>(`${BASE}/settings`);
export const addLead = (lead: NewLead) => postJson<IntakeResult>(`${BASE}/intake`, lead);

/** commit=false previews what the file holds; commit=true imports it. The body is the raw file. */
export function importLeads(file: File, commit: false): Promise<ImportPreview>;
export function importLeads(file: File, commit: true): Promise<ImportResult>;
export function importLeads(file: File, commit: boolean): Promise<ImportPreview | ImportResult> {
  return postFile<ImportPreview | ImportResult>(`${BASE}/import${commit ? "?commit=1" : ""}`, file);
}
