import { describe, expect, it } from "vitest";
import type { Connector, ConnectorStatus } from "../types";
import { actionLabel, GROUPS, statusLook, summarize } from "./connectors";

const c = (status: ConnectorStatus): Connector => ({
  id: status, group: "email", name: "", account: "", status, detail: "", basis: "", setup: { steps: [], env_keys: [] },
});

describe("connectors helpers", () => {
  it("summarizes", () =>
    expect(summarize([c("active"), c("attention"), c("available"), c("coming_soon")])).toEqual({ active: 1, attention: 1, off: 2 }));
  it("labels statuses", () => {
    expect(statusLook("available")).toEqual({ label: "Not in use", tone: "muted" });
    expect(statusLook("active")).toEqual({ label: "Active", tone: "success" });
    expect(statusLook("attention")).toEqual({ label: "Needs a look", tone: "warning" });
    expect(statusLook("coming_soon")).toEqual({ label: "Coming soon", tone: "muted" });
  });
  it("picks the action", () => {
    expect(actionLabel("active")).toBe("Manage");
    expect(actionLabel("attention")).toBe("Fix");
    expect(actionLabel("available")).toBe("Set up");
    expect(actionLabel("coming_soon")).toBe("Coming soon");
  });
  it("has the three groups in order", () => expect(GROUPS.map((g) => g.id)).toEqual(["email", "calendar", "messaging"]));
});
