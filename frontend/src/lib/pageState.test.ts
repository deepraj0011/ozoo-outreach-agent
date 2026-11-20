import { describe, expect, it } from "vitest";
import { pageState } from "./pageState";
import { ApiError } from "../api";

describe("pageState", () => {
  it("is loading before data or error", () => expect(pageState(true, null, null)).toEqual({ status: "loading" }));
  it("shows the API message when the agent is down", () =>
    expect(pageState(false, new ApiError("Cannot reach the agent. Is it running?"), null))
      .toEqual({ status: "error", message: "Cannot reach the agent. Is it running?" }));
  it("is ready with data", () => expect(pageState(false, null, { a: 1 })).toEqual({ status: "ready", data: { a: 1 } }));
});
