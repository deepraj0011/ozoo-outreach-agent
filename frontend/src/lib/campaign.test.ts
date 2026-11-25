import { describe, expect, it } from "vitest";
import { campaignHeaderState, cellLook, clip, initials, matchesQuery, stripItems, tabCounts, timingLabel }
  from "./campaign";
import type { BoardLead, Cell, ScheduleRow } from "../types";

const row = (o: Partial<ScheduleRow>): ScheduleRow => ({
  email_no: 1, mode: "day", day: 0, at: null, template_id: 1, template_name: "First note", ...o,
});

const cell = (o: Partial<Cell>): Cell => ({
  email_no: 1, status: "planned", date: null, sent_at: null, opens: 0, clicks: 0, visits: 0, reason: null,
  late: false, ...o,
});

const lead = (o: Partial<BoardLead>): BoardLead => ({
  id: 1, name: "A Lead", company: "Co", role: "", state: "in_sequence", paused: false, stop_reason: null,
  stopped_at: null, emails: [cell({})], ...o,
});

describe("timingLabel", () => {
  it("labels timings", () => {
    expect(timingLabel(row({ mode: "now" }), "Asia/Kolkata")).toBe("Right away");
    expect(timingLabel(row({ mode: "day", day: 9 }), "Asia/Kolkata")).toBe("Day 9");
    expect(timingLabel(row({ mode: "at", at: "2026-11-24T04:30:00Z" }), "Asia/Kolkata")).toBe("Tue 24 Nov, 10:00");
  });

  it("reads a local business time from the API as already in the zone", () =>
    expect(timingLabel(row({ mode: "at", at: "2026-11-24T10:00" }), "Asia/Kolkata")).toBe("Tue 24 Nov, 10:00"));
});

describe("campaignHeaderState", () => {
  it("shows Not started with a Start action before the first start", () =>
    expect(campaignHeaderState({ running: false, started_at: null, leads: 3 }))
      .toEqual({ label: "Not started", action: "start" }));
  it("shows Paused after a start", () =>
    expect(campaignHeaderState({ running: false, started_at: "2026-11-20T10:00:00Z", leads: 3 }))
      .toEqual({ label: "Paused", action: "start" }));
  it("shows Sending with a Pause action while running", () =>
    expect(campaignHeaderState({ running: true, started_at: "2026-11-20T10:00:00Z", leads: 3 }))
      .toEqual({ label: "Sending", action: "pause" }));
});

describe("stripItems", () => {
  it("has no steps for an empty schedule", () => expect(stripItems([])).toEqual([]));
  it("sorts steps by email number", () =>
    expect(stripItems([row({ email_no: 2 }), row({ email_no: 1 })]).map((s) => s.emailNo)).toEqual([1, 2]));
});

describe("cellLook", () => {
  it("maps cell statuses", () => {
    expect(cellLook("sent")).toEqual({ icon: "sent", tone: "success" });
    expect(cellLook("today")).toEqual({ icon: "today", tone: "warning" });
    expect(cellLook("planned")).toEqual({ icon: "planned", tone: "muted" });
    expect(cellLook("handed")).toEqual({ icon: "planned", tone: "muted" });
    expect(cellLook("cancelled")).toEqual({ icon: "cancelled", tone: "muted" });
    expect(cellLook("skipped")).toEqual({ icon: "cancelled", tone: "muted" });
  });
});

describe("tabCounts", () => {
  it("counts tabs", () => {
    const leads = [
      lead({ id: 1, stop_reason: "replied", emails: [cell({ status: "sent" }), cell({ email_no: 2, status: "cancelled" })] }),
      lead({ id: 2, emails: [cell({ status: "today" })] }),
      lead({ id: 3 }),
    ];
    expect(tabCounts(leads)).toEqual({ all: 3, today: 1, replied: 1, stopped: 1 });
  });
});

describe("lead text", () => {
  it("keeps short text", () => expect(clip("Konkan Rural Bank")).toEqual({ text: "Konkan Rural Bank", clipped: false }));
  it("cuts text past 28 characters", () => {
    const out = clip("Sahyadri Housing Finance Limited India");
    expect(out.clipped).toBe(true);
    expect(out.text.length).toBeLessThanOrEqual(28);
    expect(out.text.endsWith("…")).toBe(true);
  });
  it("makes initials", () => {
    expect(initials("Ritika Sharma")).toBe("RS");
    expect(initials("neha")).toBe("NE");
  });
  it("searches name and company ignoring case", () => {
    const l = lead({ name: "Arjun Kulkarni", company: "Vardhan Capital" });
    expect(matchesQuery(l, "VARDHAN")).toBe(true);
    expect(matchesQuery(l, "arjun")).toBe(true);
    expect(matchesQuery(l, "konkan")).toBe(false);
    expect(matchesQuery(l, "  ")).toBe(true);
  });
});
