import { describe, expect, it } from "vitest";
import { validateNewLead } from "./leads";

describe("validateNewLead", () => {
  it("needs name and company", () =>
    expect(Object.keys(validateNewLead({ name: "", company: "", email: "a@b.in" })).sort()).toEqual(["company", "name"]));
  it("rejects a malformed email", () =>
    expect(validateNewLead({ name: "N", company: "C", email: "nope" }).email).toBe("This email does not look right."));
  it("needs a way to reach the lead", () =>
    expect(validateNewLead({ name: "N", company: "C" }).email).toBe("Add an email or a phone number."));
  it("accepts a phone-only lead", () =>
    expect(validateNewLead({ name: "N", company: "C", phone: "+919800000000" })).toEqual({}));
});
