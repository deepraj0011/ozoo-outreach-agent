import { describe, expect, it } from "vitest";
import { businessDate, daysBetween, gapLabel, MAX_EMAILS, rowOffset, rulerLayout, weekBands, weekendNote, whenLabel }
  from "./schedule";
import type { ExampleDate, ScheduleRow } from "../types";

const TZ = "Asia/Kolkata";
const TODAY = "2026-11-25"; // a Wednesday

const row = (o: Partial<ScheduleRow>): ScheduleRow => ({
  email_no: 1, mode: "day", day: 0, at: null, template_id: 1, template_name: "First note", ...o,
});
const ex = (o: Partial<ExampleDate>): ExampleDate => ({ email_no: 1, day: 0, date: TODAY, ...o });

describe("dates", () => {
  it("allows fifteen emails", () => expect(MAX_EMAILS).toBe(15));
  it("reads the business calendar day of an instant", () =>
    expect(businessDate("2026-11-25T20:00:00Z", TZ)).toBe("2026-11-26"));
  it("counts calendar days", () => {
    expect(daysBetween("2026-11-25", "2026-12-09")).toBe(14);
    expect(daysBetween("2026-11-25", "2026-11-25")).toBe(0);
  });
});

describe("rowOffset", () => {
  it("puts Now on day 0, a Day email on its day and a date on its offset from today", () => {
    expect(rowOffset({ mode: "now", day: 0, at: null }, TODAY)).toBe(0);
    expect(rowOffset({ mode: "day", day: "9", at: null }, TODAY)).toBe(9);
    expect(rowOffset({ mode: "at", day: 0, at: "2026-12-04T10:00" }, TODAY)).toBe(9);
  });
  it("has no offset for an unreadable row; a past date counts as day 0", () => {
    expect(rowOffset({ mode: "day", day: "x", at: null }, TODAY)).toBeNull();
    expect(rowOffset({ mode: "day", day: "-2", at: null }, TODAY)).toBeNull();
    expect(rowOffset({ mode: "at", day: 0, at: "" }, TODAY)).toBeNull();
    expect(rowOffset({ mode: "at", day: 0, at: "2026-11-20T10:00" }, TODAY)).toBe(0);
  });
});

describe("whenLabel", () => {
  it("labels each kind", () => {
    expect(whenLabel(row({ mode: "now" }), TZ)).toBe("Now");
    expect(whenLabel(row({ day: 4 }), TZ)).toBe("Day 4");
    expect(whenLabel(row({ mode: "at", at: "2026-12-04T10:00" }), TZ)).toBe("Fri 4 Dec, 10:00");
  });
});

describe("rulerLayout", () => {
  const rows = [row({ email_no: 1, mode: "now" }), row({ email_no: 2, day: 4, template_name: "Follow up" }),
    row({ email_no: 3, mode: "at", at: "2026-12-14T10:00" })];
  const example = [ex({}), ex({ email_no: 2, date: "2026-11-30" }), ex({ email_no: 3, date: "2026-12-14" })];

  it("places marks proportionally with alternating labels", () => {
    const out = rulerLayout(rows, example, TODAY, TZ);
    expect(out.marks.map((m) => [m.kind, m.offset, m.above])).toEqual([["now", 0, true], ["day", 4, false],
      ["at", 19, true]]);
    expect(out.span).toBe(21);
    expect(out.marks[1].pct).toBeCloseTo((4 / 21) * 100);
    expect(out.marks[1].date).toBe("Mon 30 Nov");
    expect(out.lastDay).toBe(19);
    expect(out.endDate).toBe("Mon 14 Dec");
  });

  it("keeps fifteen daily emails apart and inside the axis", () => {
    const many = Array.from({ length: 15 }, (_, i) => row({ email_no: i + 1, day: i }));
    const out = rulerLayout(many, [], TODAY, TZ);
    const pcts = out.marks.map((m) => m.pct);
    expect(pcts[0]).toBe(0);
    expect(Math.max(...pcts)).toBeLessThanOrEqual(100);
    pcts.slice(1).forEach((p, i) => expect(p).toBeGreaterThan(pcts[i]));
  });

  it("nudges emails on the same day apart", () => {
    const out = rulerLayout([row({ email_no: 1, mode: "now" }), row({ email_no: 2, day: 0 })], [], TODAY, TZ);
    expect(out.marks[1].pct).toBeGreaterThan(out.marks[0].pct);
  });

  it("keeps neighbours at least the given gap apart", () => {
    const daily = Array.from({ length: 15 }, (_, i) => row({ email_no: i + 1, day: i }));
    const pcts = rulerLayout(daily, [], TODAY, TZ, 100 / 15).marks.map((m) => m.pct);
    pcts.slice(1).forEach((p, i) => expect(p - pcts[i]).toBeGreaterThanOrEqual(100 / 15 - 1e-9));
    expect(pcts[14]).toBeLessThanOrEqual(100);
  });

  it("is empty without a schedule", () => expect(rulerLayout([], [], TODAY, TZ).marks).toEqual([]));
});

describe("weekBands", () => {
  it("splits the span into alternating weeks", () =>
    expect(weekBands(21)).toEqual([{ week: 1, from: 0, to: (7 / 21) * 100, shaded: false },
      { week: 2, from: (7 / 21) * 100, to: (14 / 21) * 100, shaded: true },
      { week: 3, from: (14 / 21) * 100, to: 100, shaded: false }]));
});

describe("gapLabel", () => {
  it("says the days since the previous email", () => {
    expect(gapLabel(null, 0)).toBe("");
    expect(gapLabel(0, 4)).toBe("+4 days");
    expect(gapLabel(4, 5)).toBe("+1 day");
    expect(gapLabel(4, 4)).toBe("same day");
    expect(gapLabel(4, null)).toBe("");
  });
});

describe("weekendNote", () => {
  it("says when a weekend day moves to Monday", () =>
    expect(weekendNote({ mode: "day", day: "3", at: null }, ex({ date: "2026-11-30" }), TODAY))
      .toBe("Day 3 is a Saturday: goes Monday"));
  it("says nothing when the day is the send day", () =>
    expect(weekendNote({ mode: "day", day: "5", at: null }, ex({ date: "2026-11-30" }), TODAY)).toBe(""));
  it("says a fixed date is the same for every lead", () =>
    expect(weekendNote({ mode: "at", day: 0, at: "2026-12-04T10:00" }, ex({ date: "2026-12-04" }), TODAY))
      .toBe("Same moment for every lead"));
});
