import { describe, expect, it } from "vitest";
import { campaignHeaderState, clip, dotHint, dotLook, initials, matchesQuery, nextLabel, sentCount,
  tabCounts, timingLabel, trackLabel } from "./campaign";
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

const TZ = "Asia/Kolkata";

describe("nextLabel", () => {
  it("names the next email and its day", () =>
    expect(nextLabel(lead({ emails: [cell({ status: "sent", sent_at: "2026-11-20T05:00:00Z" }),
      cell({ email_no: 6, date: "2026-12-07T04:30:00Z" })] }), TZ)).toBe("Email 6 · Mon 7 Dec"));
  it("gives the time when the next email goes today", () =>
    expect(nextLabel(lead({ emails: [cell({ email_no: 4, status: "today", date: "2026-11-25T09:30:00Z" })] }), TZ))
      .toBe("Email 4 · today 3 pm"));
  it("says a late email is late", () =>
    expect(nextLabel(lead({ emails: [cell({ email_no: 2, late: true, date: "2026-11-23T04:30:00Z" })] }), TZ))
      .toBe("Email 2 · Mon 23 Nov · late"));
  it("says why emails stopped", () => {
    expect(nextLabel(lead({ stop_reason: "replied", stopped_at: "2026-11-27T06:00:00Z" }), TZ))
      .toBe("Replied 27 Nov — emails stopped");
    expect(nextLabel(lead({ stop_reason: "unsubscribed" }), TZ)).toBe("Unsubscribed — stopped");
    expect(nextLabel(lead({ stop_reason: "booked" }), TZ)).toBe("Demo booked — emails stopped");
    expect(nextLabel(lead({ stop_reason: "stopped", stopped_at: "2026-11-27T06:00:00Z" }), TZ))
      .toBe("Stopped 27 Nov — no more emails");
  });
  it("says paused before the next email", () => expect(nextLabel(lead({ paused: true }), TZ)).toBe("Paused"));
  it("says all sent, and nurture when the lead moved there", () => {
    const sent = [cell({ status: "sent" }), cell({ email_no: 2, status: "sent" })];
    expect(nextLabel(lead({ emails: sent, state: "nurture" }), TZ)).toBe("All sent · nurture");
    expect(nextLabel(lead({ emails: sent }), TZ)).toBe("All sent");
  });
  it("says nothing is planned for a lead without emails", () =>
    expect(nextLabel(lead({ emails: [] }), TZ)).toBe("Nothing planned"));
});

describe("dots", () => {
  it("looks", () => {
    expect(dotLook(cell({ status: "sent" }))).toBe("sent");
    expect(dotLook(cell({ status: "today" }))).toBe("active");
    expect(dotLook(cell({ status: "handed" }))).toBe("active");
    expect(dotLook(cell({ status: "planned" }))).toBe("planned");
    expect(dotLook(cell({ status: "planned", late: true }))).toBe("late");
    expect(dotLook(cell({ status: "cancelled" }))).toBe("stopped");
    expect(dotLook(cell({ status: "skipped" }))).toBe("stopped");
  });
  it("describes a sent email with its numbers", () =>
    expect(dotHint(cell({ status: "sent", sent_at: "2026-11-24T05:00:00Z", opens: 2, clicks: 1 }), TZ))
      .toBe("Email 1: sent Tue 24 Nov · 2 opens · 1 click"));
  it("describes planned, late and cancelled emails", () => {
    expect(dotHint(cell({ email_no: 3, date: "2026-12-07T04:30:00Z" }), TZ)).toBe("Email 3: planned Mon 7 Dec");
    expect(dotHint(cell({ email_no: 3, date: "2026-11-23T04:30:00Z", late: true }), TZ))
      .toBe("Email 3: late, was due Mon 23 Nov");
    expect(dotHint(cell({ email_no: 4, status: "cancelled", reason: "reply" }), TZ)).toBe("Email 4: cancelled (reply)");
    expect(dotHint(cell({ email_no: 4, status: "today", date: "2026-11-25T09:30:00Z" }), TZ))
      .toBe("Email 4: goes today 3 pm");
  });
  it("labels the track header", () => {
    expect(trackLabel(row({ mode: "now" }))).toBe("D0");
    expect(trackLabel(row({ day: 4 }))).toBe("D4");
    expect(trackLabel(row({ mode: "at", at: "2026-12-14T10:00" }))).toBe("14/12");
  });
  it("counts sent emails, leaving out ones removed from the schedule", () =>
    expect(sentCount(lead({ emails: [cell({ status: "sent" }), cell({ email_no: 2 }),
      cell({ email_no: 3, status: "cancelled", reason: "removed" })] }))).toEqual({ sent: 1, total: 2 }));
});
